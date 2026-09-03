# 0001. The autoscaler seed survives because the tenant HelmRelease applies client-side

- **Number:** `0001`
- **Date:** `2026-09-02`
- **Status:** Accepted
- **Deciders:** `@scooby87, @lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#66`](https://github.com/cozystack/community/pull/66)
- **Implemented in:** [`cozystack/cozystack#3954`](https://github.com/cozystack/cozystack/pull/3954)

## Context

The proposal's §3 committed to *omitting* `.spec.instances` from the rendered engine CR under active autoscaling. With the field absent from the manifest, Flux would neither set nor revert it, and the HPA — writing the CNPG `Cluster`'s `/scale` subresource — would be its sole writer. That was the whole ownership argument, and on paper it deleted the contested-field problem outright.

Building it against the current platform disproved that a chart-only omit is safe. Two facts were in play. First, the platform's helm-controller was on `v1.5.0` (`internal/fluxinstall/manifests/fluxcd.yaml`), which defaults new HelmReleases to server-side apply and hardcodes `ForceConflicts = ServerSideApply` (`internal/action/install.go:53`, same in `upgrade.go`/`rollback.go`), so any field the chart *renders* is force-owned by Flux and reverted from the HPA's live value on every apply — and Flux performs an apply on every chart or values change, which every platform release triggers. Second, the omit handoff assumed the HPA already owned `.spec.instances` at the moment the field left the manifest; but an HPA claims the `/scale` field manager only when it actually writes (`rescale = desired != current`), so a cluster idle at its floor never writes, never owns the field, and the phase-2 omit prunes it — whereupon CNPG's CRD default (`instances: 1`) takes over and the quorum webhook rejects the cluster or standbys and their PVCs are shed. Closing that gap chart-only would need the runtime actor the design had deliberately removed.

A constant-seed alternative was therefore explored, and a reviewer ([@IvanHunters](https://github.com/cozystack/community/pull/66)) raised the objection that reopened the question: forcing client-side apply does not make a rendered constant a no-op, because Helm's three-way JSON merge computes its add-and-change delta from the *live* object, so an unchanged constant would still be re-asserted against a drifted live count. That objection is the reason this record exists.

## Decision

Under active autoscaling the chart renders `.spec.instances` as a constant seed `max(replicas, effectiveMin)` (never omitted), and the tenant HelmRelease applies **client-side**, forced per application by `release.cozystack.io/helm-server-side-apply: "false"` on the `ApplicationDefinition`. Under client-side apply helm-controller patches the CNPG `Cluster` — an unstructured/CRD object — from the previous-versus-new rendered manifest and does not consult live state, so the unchanged constant produces no patch and the value KEDA writes through `/scale` survives every reconcile.

## Why not the alternatives

- **Chart-only omit under active autoscaling (the proposal's own §3).** Lost on a structural fact, not a packaging detail: on helm-controller v1.5.0 a rendered field is force-owned under SSA and reverted, and the phase-2 omit prunes `.spec.instances` for any cluster idle at its floor (the HPA has not written `/scale`, so owns nothing), collapsing it to the CNPG default of 1. Both failure modes are unavoidable without a runtime writer the design removed. Verified live on dev9.
- **Constant seed under the platform-default server-side apply.** Same force-revert: SSA with `ForceConflicts` steals the rendered field back from `/scale` on every apply. Reproduced directly — a CRD grown out of band to 4 and re-applied via `helm upgrade --server-side --force-conflicts` with the chart rendering the lower constant reverted to the constant in one step.
- **Constant seed under client-side, accepting the reviewer's "three-way re-asserts from live" reading.** Refuted at the source. Helm v4's `createPatch` (`helm.sh/helm/v4 pkg/kube/client.go`) routes unstructured/CRD objects to a **two-way** `jsonpatch.CreateMergePatch(original, modified)` — the previous rendered manifest against the new one, live never read — and only takes the three-way `CreateThreeWayJSONMergePatch(original, modified, current)` path the objection quotes when `threeWayMergeForUnstructured` is set. helm-controller v1.5.0 never sets it (no reference anywhere in the controller), so the default two-way path applies. Confirmed empirically on Helm 4.0.4 (the same SDK helm-controller v1.5.0 embeds): a custom resource drifted to 4 out of band, then `helm upgrade --server-side=false` with the chart rendering the constant 2 *and* changing an unrelated field — the field change applied, `spec.instances` stayed 4. Only `--server-side --force-conflicts` reverted it.
- **Pinning `.spec.instances` via the scale subresource across the apply (the proposal's named fallback).** Rejected: it re-introduces a runtime writer of the CR's spec, which is exactly the property §3 set out to eliminate. Client-side apply achieves the same guarantee with no actor.

## Consequences

- A new platform knob: a per-`ApplicationDefinition` annotation (`release.cozystack.io/helm-server-side-apply`) threaded into `Install`/`Upgrade.ServerSideApply` on the emitted HelmRelease (`pkg/config`, `pkg/cmd/server/start.go`, `pkg/registry/apps/application/rest.go`). It is general, not postgres-specific.
- The annotation is **per-kind**, so client-side apply reaches *every* Postgres, not only autoscaled ones. That trades away, for Postgres specifically, the SSA benefit v1.5.0 introduced — misplaced chart fields becoming hard errors rather than silently dropped (`docs/changelogs/v1.5.0.md`). Accepted because Postgres shipped on client-side apply before v1.5.0 without issue; a per-instance apply strategy that keeps SSA on for non-autoscaled databases is a possible follow-up.
- One residual write path remains under client-side too: because the two-way patch propagates any change between the previous and new *manifest*, editing `replicas`, `autoscaling.minReplicas` or `quorum.maxSyncReplicas` moves the seed, so that single reconcile does patch `.spec.instances` to the new seed (never below the quorum floor, never a collapse to 1). This is documented as the floor-change caveat in the chart's `values.yaml` and §Upgrade, with a suspend-and-pin recipe for a deliberate rebase.
- Behaviour no longer splits by install date. Because the annotation forces `Install.ServerSideApply=false` and `Upgrade.ServerSideApply=disabled` uniformly, both pre- and post-v1.5.0 Postgres releases apply client-side; there is no population that silently stayed on SSA.

## Revisit if

helm-controller starts enabling `threeWayMergeForUnstructured` by default (the client-side patch would then read live state and re-assert the constant, breaking the no-op — the exact failure the reviewer described, gated today only by that flag's default); or a per-instance apply-strategy mechanism lands, which would let SSA remain on for Postgres databases that do not use autoscaling.

---

<!--
Format follows Michael Nygard's architecture decision records
(https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
and MADR (https://adr.github.io/madr/).

Once merged, the header block above is maintained (Status, Superseded by, Implemented in must track reality) and the prose below it is frozen — with one active exception: "Revisit if" stays a live trigger. When one of its conditions occurs the decision is revisited by *superseding* this record — a new ADR plus a Status / "Superseded by" bump in the maintained header — never by editing the frozen prose in place. The gate to act on is therefore the header, not the frozen body.

-->
