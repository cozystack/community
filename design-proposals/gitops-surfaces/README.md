# GitOps Configuration Surfaces for Cozystack

- **Title:** `GitOps Configuration Surfaces: admin and tenant self-service from git`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-06-17`
- **Status:** Draft

## Overview

Cozystack ships a complete Flux control plane but exposes almost none of it to the people running the cluster. Admins configure the platform by hand-patching Package CRs and applying raw manifests with `kubectl`; tenants configure their apps through the dashboard or one-off `kubectl apply`. Both paths produce configuration drift and neither is reproducible. Meanwhile the source-controller and kustomize-controller needed to reconcile a git repository are *already running* in the management cluster — they are just not offered as a product surface.

This proposal defines a single primitive — **a reconciled GitOps surface** (`GitRepository` + `Kustomization` + a scoped `ServiceAccount`) — and instantiates it at two trust tiers: a **platform GitOps** surface for admins (cluster scope) and a **tenant GitOps** surface for tenant users (namespace scope). It also proposes an **admin override layer** so that the most common admin task — changing component parameters — does not require fighting Helm over field ownership. The first increment (the admin surface) is already prototyped in [cozystack/cozystack#2731](https://github.com/cozystack/cozystack/pull/2731).

## Scope and related proposals

- **First increment — admin surface:** [cozystack/cozystack#2731](https://github.com/cozystack/cozystack/pull/2731) (`feat(platform): add cozystack.customizer system package`) implements Tool 1, Phase 1. This proposal generalizes that PR and gives it a home in the larger model.
- **Deferred to follow-up proposals** (intentionally not designed in full here): **Package** field-ownership enforcement via `ValidatingAdmissionPolicy` / admission webhook on `packages.cozystack.io` (Tool 1; sketched under Security, full design separate) — this is distinct from the tenant-`Kustomization` admission enforcement in Tool 2, which is **in-scope** and ships with that surface; and a delegated mid-tier between cluster-admin and tenant (team-scoped admins).
- This proposal should land before the tenant surface (Tool 2) is implemented, because the unifying primitive and the multitenancy-lockdown decision are shared.

## Context

Cozystack delivers everything through Flux. The relevant pieces today:

- **Management Flux is real and complete.** `internal/fluxinstall` (embedded in the cozystack-operator) installs source-controller, kustomize-controller, helm-controller, and notification-controller into the `cozy-fluxcd` namespace, watching all namespaces. An admin-created `GitRepository` + `Kustomization` in `cozy-system` reconciles today with no new controllers. API versions in use: `source.toolkit.fluxcd.io/v1`, `kustomize.toolkit.fluxcd.io/v1`, `helm.toolkit.fluxcd.io/v2`.
- **The Package CRD is the platform's config unit.** `api/v1alpha1/package_types.go` (`scope=Cluster`). A `Package` selects a variant and carries component values. Two very different lifecycles exist:
  - **The bootstrap Package** `cozystack.cozystack-platform` is **created by hand** at install time — operators copy `packages/core/installer/example/platform.yaml`, edit `spec.variant` and `spec.components.platform.values`, and `kubectl apply` it. No controller owns its spec.
  - **Downstream Packages** (`cozystack.linstor`, `cozystack.cilium`, …) are **rendered by the platform chart** (`packages/core/platform`) with `helm.sh/resource-policy: keep` and are owned by helm-controller.
- **Most admin config is Package values, not raw resources.** VM golden images live in `packages/system/vm-default-images` values; LINSTOR parameters in `packages/system/linstor` values; cert-manager `ClusterIssuer`s are driven by `packages/core/platform` values → the `cozystack-values` Secret in `cozy-system` → the `cert-manager-issuers` chart.
- **Management-plane Flux is the embedded flux-aio, and helm-controller is already sharded by an operator.** The management cluster runs a single all-in-one `flux` Deployment installed from `internal/fluxinstall`; its source-, kustomize-, and helm-controllers carry `--watch-label-selector=!sharding.fluxcd.io/key`, so any object *labelled* with that key is skipped by the unrestricted main plane. **flux-shard-operator** provisions auto-scaled `helm-controller-shard<i>` Deployments (each `--watch-label-selector=sharding.fluxcd.io/key=shard<i>`), assigns each tenant to a shard, and, via a mutating webhook, rewrites the birth label `sharding.fluxcd.io/key: tenants` on tenant HelmReleases to the assigned `shard<i>`. It has **retired the old hand-rolled `flux-tenants` Deployment** (migration 44) — so tenant HelmReleases are no longer one-controller-per-tenant but packed onto a shared, auto-scaled pool. Crucially, **there is no kustomize-controller sharding**: a `Kustomization` *without* the shard label is reconciled by the unrestricted main kustomize-controller.
- **Tenants cannot self-GitOps.** A `Tenant` (`apps.cozystack.io`) produces a `tenant-<name>` namespace, a ServiceAccount named `<tenant-name>`, and `cozy:tenant:*` ClusterRoles. Tenants create curated **Application** CRs (e.g. Postgres, Kafka, VM) which the aggregated apiserver converts into the HelmReleases above. Tenants have **no RBAC for `source`/`kustomize`/`helm` toolkit CRDs** and there is **no tenant-scoped source/kustomize reconciliation** — the only isolation a tenant has today is precisely that it cannot author Flux objects at all. (The flux-operator `FluxInstance` with `multitenant: false` in `packages/system/fluxcd` governs **child Kubernetes clusters**, not this management plane — migration 21 retired the FluxInstance path here in favor of the embedded flux-aio. This resolves Open Question 1 below.)

### The problem

- *"I changed the LINSTOR auto-diskful timeout on a node by hand last month and now I can't remember which clusters got it."* — admin config is imperative and drifts.
- *"To set up DR I'd need to reconstruct every `kubectl patch` I ever ran."* — there is no single source of truth for cluster configuration.
- *"My team already keeps our app manifests in git. I want Cozystack to reconcile them into my tenant, but all I can do is click in the dashboard or `kubectl apply` and hope nobody else changed it."* — tenants have no GitOps path even though Flux is right there.
- *"I patched a downstream Package's values and Helm silently took the field back on the next reconcile (or I silently took Helm's)."* — patching chart-owned Packages races helm-controller because kustomize-controller hardcodes `client.ForceOwnership`.
- *"I already run my own GitOps (ArgoCD / plain Flux) against this cluster, but the only way to override a bundle-shipped Package is to delete it and recreate it from my repo."* — there is no override layer that composes with external GitOps tooling, so bring-your-own-GitOps users fight the platform's own Packages.

## Goals

- Provide an opt-in, declarative GitOps surface for **admins** that reconciles an admin-owned repo into the management cluster.
- Provide an opt-in, declarative GitOps surface for **tenants** that reconciles a tenant-owned repo into that tenant's namespace, isolated to that tenant's existing RBAC.
- Make the common admin task — overriding component parameters (versions, golden-image values, LINSTOR options) — possible **without** racing helm-controller for field ownership.
- Define one shared primitive and reuse it across tiers, rather than shipping unrelated one-off tools.
- Keep all surfaces **off by default**; an operator opts in per cluster / per tenant.
- Establish an honest, documented trust model for each tier.

### Non-goals

- This proposal does **not** reduce the capability of an admin below what they already have by hand. The admin surface is a cluster-admin-equivalent credential by design.
- It does **not** fully specify the field-ownership admission policy (separate proposal).
- It does **not** introduce a per-tenant Flux *instance*; tenant reconciliation reuses the shared controllers with isolation enforced by RBAC + lockdown.
- It does **not** add multi-cluster / fleet management.

## Design

### The unifying primitive

Every use case is the same atom:

```
GitRepository + Kustomization + a scoped ServiceAccount → reconciled by cozy-fluxcd
```

Only two things vary: **(a)** the trust level of the ServiceAccount and **(b)** the isolation guarantees enforced on its Kustomization. We therefore build one parameterized chart and bind it to different SAs at different scopes.

```mermaid
flowchart TD
    subgraph repo_admin[Admin git repo]
      A1[Package overrides<br/>raw cluster resources<br/>policies, issuers, BYO HelmReleases]
    end
    subgraph repo_tenant[Tenant git repo]
      T1[Application CRs<br/>namespaced manifests]
    end

    A1 -->|GitRepository + Kustomization<br/>SA: cozystack-customizer| KC[(main kustomize-controller<br/>cozy-fluxcd)]
    T1 -->|GitRepository(s) + Kustomization(s)<br/>SA: tenant-gitops| KCT[(dedicated kustomize-controller<br/>per gitops-enabled tenant<br/>in cozy-fluxcd<br/>forced SA + no-cross-ns-refs)]

    KC -->|cluster scope| CLUSTER[(Management cluster<br/>Packages, cluster resources)]
    KCT -->|namespace scope only| NS[(tenant-name namespace)]
```

### Tool 1 — Platform GitOps (admin, cluster scope)

Generalizes [#2731](https://github.com/cozystack/cozystack/pull/2731). A system package (`cozystack.customizer`) provisions, in `cozy-system`: a `GitRepository` pointing at an admin-owned repo (auth Secret pre-created by the admin); a `Kustomization` reconciled by a curated `cozystack-customizer` ServiceAccount; and the RBAC that SA needs.

**Trust model (explicit):** write access to the admin repo is equivalent to cluster-admin. This is not a weakening of the platform — an operator who can `kubectl patch` already has this power. The surface *legitimizes and records* what was previously done by hand. Documentation must state this plainly, so the repo and its git credentials are protected like a kubeconfig.

This tool covers admin use cases directly: changing platform parameters (UC1), managing arbitrary cluster resources outside the core API — custom `ClusterIssuer`s, LINSTOR `StoragePool`s, BYO `HelmRelease`s, `NetworkPolicy`, custom CRDs (UC3) — plus secrets-as-code, policy-as-code, environment overlays, and ultimately whole-cluster reproducibility / DR.

#### Package ownership: two classes, two stories

The single most important design point. RBAC is object-level; the contract we need is field-level. The two Package lifecycles diverge here:

| | bootstrap `cozystack.cozystack-platform` | downstream `cozystack.linstor`, … |
|---|---|---|
| Created by | hand (`kubectl apply` from the installer example) | the platform chart (`resource-policy: keep`) |
| Spec field manager | the operator — **no controller** | **helm-controller** |
| Force-ownership risk | **none** — nothing competes | **real** — claiming a chart-owned field steals it |

- **Bootstrap Package:** because no controller owns its spec, the admin repo can simply **own it**. The recommended flow is *self-adoption*: bootstrap once by hand with the customizer enabled, then commit the same Package manifest to the repo; from then on the repo is its source of truth (the standard `flux bootstrap` pattern). Use Server-Side Apply with a *partial* Package manifest to own only the fields you manage.
- **Downstream Packages:** patching them races helm-controller. We do **not** solve this by denying access — that would re-introduce hand-management of exactly the parameters admins care about, and this is an admin tool. We solve it with an override layer (below); a field-ownership admission policy is an *optional* guard for teams that want one, never a wall that removes reachable knobs.

#### Admin override layer (for UC1b — component parameters)

Today, changing a downstream component's parameters (e.g. `linstor.autoDiskful.minutes`, a golden-image URL, a pinned version) means patching a *chart-owned* Package — the contested case. We solve it by **splitting the override layer out of the Package into its own resource**, extending the existing API with a third kind:

| kind | owner | role |
|---|---|---|
| `PackageSource` | platform (unchanged) | charts + variants |
| `Package` | the platform chart (`resource-policy: keep`) | the reconciled, system-owned layer |
| `PackageValues` (new) | the admin repo | the human-owned override layer |

A `PackageValues` object carries per-package admin overrides; the operator merges it over the matching Package's values when it builds the HelmRelease:

```yaml
apiVersion: cozystack.io/v1alpha1
kind: PackageValues
metadata:
  name: cozystack.linstor      # 1:1 with the target Package by name
spec:
  components:
    linstor:
      values:
        autoDiskful:
          minutes: 10
```

Because the chart never writes `PackageValues`, and an admin using it never needs to touch the chart-owned `Package`, **there is no field for the admin and helm-controller to fight over on the override path** — the force-ownership race is removed for that path, not merely sidestepped. Admins are not *prevented* from editing a chart-owned `Package` directly — that stays possible by design (see the security posture and edge cases); `PackageValues` simply offers a first-class path that does not race. This is *not* a sibling `cozystack-values`-style Secret (the operator hardcodes a single `valuesFrom` and resets others); `PackageValues` is a first-class CR the operator itself merges into `hr.Spec.Values`, so it cooperates with the existing reconcile path. Because that merge lives in the **cozystack-operator**, not in the customizer's reconcile path, the override layer composes with **any** GitOps tooling that can create a `PackageValues` object — the customizer, ArgoCD, or plain Flux — and is **not coupled to the admin surface**; bring-your-own-GitOps operators get UC1b without adopting Tool 1 (this is the case the last problem bullet describes).

**Why merge at HelmRelease build, not onto the `Package`.** helm-controller applies the rendered `Package` objects via **server-side apply** (Flux 2.8 — `helm-controller` v1.5.0 defaults SSA on install and `auto`, i.e. inherit, on upgrade), and `Package.spec.components.*.values` is `x-kubernetes-preserve-unknown-fields`, so field ownership on the `Package` is tracked *per key*. That makes a materialize-onto-Package variant genuinely feasible — the operator could SSA-apply `PackageValues` onto the `Package` and let a real key overlap surface as a loud SSA conflict (recorded under Alternatives). We deliberately do **not** adopt it as the default: injecting one layer *below* Package ownership (into `hr.Spec.Values`) keeps the admin override **authoritative** — the point of an admin tool — while *never* contending for a chart-owned field, so it is race-free. Materializing onto the `Package` without `force` would instead make the **chart** win any overlap and push the admin back onto the racy direct-edit path for exactly the keys they meant to override; making the admin win *while* materializing would require `force` — the `ForceOwnership` race this proposal exists to remove. Admin-authority and race-freedom are jointly reachable only below the ownership layer.

The split is **additive**: with no `PackageValues` present, the operator path is byte-identical to today, so existing clusters are unaffected (see Upgrade compatibility). It also makes ownership *expressible* with ordinary object-level RBAC — the admin GitOps SA can be granted write on `PackageValues` and read-only on `Package` — an opt-in discipline for teams that want their overrides confined to the human-owned layer, and the foundation for whole-cluster reproducibility (UC5). It is a discipline, not a cage: an admin who needs a knob that lives only on the chart-owned `Package` can still be granted it.

**Merge policy (fixed).** `PackageValues` merges over the Package's values with the precedence `chart defaults < cozystack-values < Package < PackageValues`. Maps merge deep; **lists replace wholesale** (standard Helm semantics) — so an override that sets one entry of a list (e.g. a single image under `vm-default-images`) replaces the entire list, and a caller who means to *add* one must restate the full list. Keyed or append-merge for a specific list is an explicit per-package opt-in, never implicit. `PackageValues` is the **single** override mechanism: the earlier `packageOverrides`-on-the-bootstrap-Package sketch is dropped in its favor (it collapsed all config onto one god-object with all-or-nothing RBAC and pushed this list-merge correctness into per-package templates — see Alternatives).

**Visibility and lifecycle.** Merging at build time (rather than writing back onto the `Package`) means the `Package` spec stays chart-owned and pristine — but the *effective* values then live only in the built HelmRelease. To keep `kubectl get package` a single place to read the result, the operator surfaces the effective merged values, and which `PackageValues` contributed, on the **`Package` status** (operator-owned — no contention with helm-controller's spec ownership) and a printer column. A `PackageValues` whose target `Package` does not exist is **not** a silent no-op: the operator reports it as a status condition on the `PackageValues`, so a typo'd package name (or an override applied before its Package exists) surfaces loudly instead of disappearing.

### Tool 2 — Tenant GitOps (tenant, namespace scope)

Delivered as a **catalog app**, not a `Tenant` API change and not a parent-gated "extra module" — it is opt-in per tenant, self-service, and fits the existing app model. A single instance takes a **list of sources** (one or more `GitRepository` + `Kustomization` pairs), so a tenant never needs to instantiate the app more than once: the natural shape is **one instance ⇒ one dedicated controller per gitops-enabled tenant**, which dissolves the "5 instances = 5 controllers?" capacity worry without gating the feature behind the parent. If an admin wants a hard cap, a per-namespace `ResourceQuota` (or a one-instance-per-namespace admission check) bounds it — a restriction added where needed, rather than trading away tenant self-service by default (see Alternatives). Unlike Tool 1 it is *not* a no-op on the control plane: tenants hold **no** RBAC for Flux CRDs today, so the surface must grant Kustomization write, and granting it is exactly what opens the isolation problem below. The enforcement therefore ships **with** the surface, not as later hardening.

- New app package `packages/apps/gitops` + ApplicationDefinition in `packages/system/gitops-rd`. When a tenant instantiates it, the chart renders, in the tenant namespace, one or more `GitRepository` + `Kustomization` pairs bound to a dedicated **`<tenant>-gitops` `ServiceAccount`** (see *A dedicated, narrow GitOps SA* below), and the tenant is provisioned **its own dedicated kustomize-controller** (running platform-side — see the source-path isolation point).
- Tenants point the surface at a repo of Application CRs and namespaced manifests. Because every reconcile runs as the `<tenant>-gitops` SA, a tenant can only create `apps.cozystack.io` Application CRs inside its own namespace subtree — but that guarantee holds only if nothing lets a tenant Kustomization escape onto the unrestricted main controller or borrow a foreign SA (see below).

#### Isolation: a dedicated controller + SA per tenant, enforced (not by discipline)

The helm-controller shard pool can pack many tenants onto a few auto-scaled shards because it *impersonates* nothing — tenants never author HelmReleases directly. Kustomizations are different: the surface hands tenants the right to create them, and a kustomize-controller reconciles their contents **as a named SA**. A single shared, pooled controller would therefore have to impersonate every tenant's SA — a fat, cross-tenant privilege and a single blast radius for all tenants. So the Kustomization path is deliberately **not** the helm-controller pool; it is a **separate sharding path with its own provisioning and its own logic**, running alongside (never merged into) the helm-controller shard machinery. flux-shard-operator already owns an equivalent placement/provisioning/webhook skeleton for helm-controller, so this reuses the pattern rather than inventing one.

**A dedicated, narrow GitOps SA.** The surface does **not** reuse the interactive tenant SA. The default tenant SA carries `cozy:tenant:base`, which grants more than app management — full write on `sdn.cozystack.io/securitygroups` plus assorted cluster reads — so binding the git path to it would hand a repo more than it needs. Instead the app binds a purpose-built **`<tenant>-gitops` ServiceAccount** with write on `apps.cozystack.io` only (plus read for status). The git path is therefore *strictly narrower* than an interactive user, and the "git can only produce Application CRs" guarantee (Open Question 2) becomes **explicit RBAC** rather than an emergent property of the current role set.

1. **A dedicated kustomize-controller per gitops-enabled tenant.** Enabling the app provisions a kustomize-controller scoped to that tenant, running **in the platform namespace (`cozy-fluxcd`)** — not the tenant namespace (see the source-path point) — watching only that tenant's shard label, started with `--no-cross-namespace-refs`, pinned so every Kustomization reconciles as the `<tenant>-gitops` SA. It is opt-in, so the controller count tracks only the tenants who use GitOps — not every tenant — which is what makes a per-tenant (rather than pooled) controller affordable here.
2. **A separate mutating webhook** (distinct from the helm-controller shard webhook) stamps the tenant's shard label **and** forces `spec.serviceAccountName` on Kustomizations created in tenant namespaces, on **CREATE and UPDATE**. Because the surface grants tenants Kustomization write, this webhook is a **security boundary**, so it runs **fail-closed** (`failurePolicy: Fail`) — unlike the helm-controller webhook, which can fail-open safely since tenants cannot author HelmReleases.
3. **A `ValidatingAdmissionPolicy` as an independent backstop.** On **CREATE and UPDATE** (so create-valid-then-strip-the-label is not an escape) the VAP rejects any tenant-namespace Kustomization whose shard-label **value** is not the tenant's own shard — mere *presence* is insufficient, since a wrong value would route the Kustomization to another tenant's controller — or that names a foreign SA. Webhook (mutate → correct) plus VAP (validate → reject) close the "omit/alter the label to reach another controller" and "borrow a privileged SA" evasions **by construction**, rather than documenting them as advisory.
4. **The source path stays platform-side and closed to tenant workloads.** A tenant `GitRepository` carries no shard label, so it is reconciled by the shared main **source-controller** in `cozy-fluxcd` (there is no source-controller sharding) — a *platform* component now fetching tenant-supplied URLs (an SSRF surface) and a shared DoS target (large repos / aggressive intervals). Three decisions contain this: (a) the per-tenant kustomize-controller runs in **`cozy-fluxcd`**, not the tenant namespace, so opening it to the artifact endpoint does not open every tenant pod to it; (b) the source-controller artifact endpoint — served **unauthenticated over HTTP** at `flux.cozy-fluxcd` — is reachable only from the flux control plane; tenant namespaces get **no** egress to it (Cilium policy), so a tenant workload can never pull another tenant's or the platform's artifacts; (c) tenant `GitRepository` interval/size limits bound the shared-source DoS. A per-tenant source-controller is possible but not warranted at first; the placement + closed-artifact contract is what must be stated (this is the part of the isolation model that was previously specified only for kustomize-controller).

### Cross-cutting capabilities

- **Secrets-as-code:** SOPS/age decryption (the customizer already exposes a `decryption` block) for admins; per-namespace decryption keys (or external-secrets) for tenants. One design, two scopes.
- **Status surfacing:** expose `Kustomization` / `GitRepository` sync status in the dashboard via a `TenantModule`-style read-only view, so admins and tenants see drift without raw Flux access.
- **Dashboard ↔ git is one-way (drift is corrected *toward* git).** Once an Application is git-managed, a dashboard edit is silently reverted on the next reconcile — a confusing UX if unaddressed. Minimum: the dashboard marks git-managed Applications **read-only** (detected via field manager or a label) so the edit is refused up front rather than lost. A future two-way model — dashboard edits become commits/PRs back to the tenant repo (the ReverseGitOps approach demoed at a Cozystack meetup, reversegitops.dev) — is out of scope here but noted as the intended direction.

## User-facing changes

- **Admins:** a single `customizer.enabled` flag (plus `source`/`kustomization`/`rbac` blocks) in the platform values, off by default. When enabled, the admin manages the cluster from a git repo; component parameters are set via `PackageValues` resources.
- **Tenants:** a new **GitOps** entry in the app catalog. Instantiating it (once) lets a tenant register **one or more** git repos that reconcile into their namespace. Sync status appears in the dashboard, and Applications that are git-managed become read-only in the UI (edits go through git).
- **Docs:** a new `operations/customizer` (admin) page and a tenant GitOps how-to, including the explicit trust model and the bootstrap-vs-downstream ownership rules, plus a **reference admin-repo layout** (directory structure for Package overrides / raw cluster resources / environment overlays) so operators do not each invent their own — an example repo is a pre-GA deliverable, otherwise the "environment overlays" use case stays theoretical.

## Upgrade and rollback compatibility

- Fully **additive and opt-in**; existing clusters are unaffected until an operator enables a surface. No migration required.
- The `PackageValues` resource is additive: with none present, the operator builds HelmReleases exactly as today, so charts use their current defaults.
- **Rollback:** disabling a surface stops emitting its resources. Note that platform Packages carry `helm.sh/resource-policy: keep`, so the customizer Package and its child objects are **not** auto-deleted on disable — teardown is a documented manual step (this is existing platform behavior, called out here so it is not surprising).
- Self-adoption of the bootstrap Package is reversible: stop reconciling it from the repo and resume hand-management; nothing about the object changes.

## Security

This is the heart of the proposal; each tier has a distinct, **explicit** trust boundary.

- **Tool 1 (admin):** repo write == cluster-admin, by design and by documentation. The curated ClusterRole in the prototype (patch-not-delete on Packages, no CRDs, etc.) is *defense against accident, not against intent* — anyone with repo write can escalate, so the docs must not over-claim a boundary. New trust surface introduced: an admin git repo + its credentials (protect like a kubeconfig), and a standing, self-healing reconciling credential whose blast radius equals an admin's.
- **Tool 1 posture (deliberately permissive — it is an admin tool):** the customizer is a cluster-admin-equivalent credential, so by design it manages `cozy-system` and the platform's own supply chain the same way Flux manages its own manifests — this is intentional, not a boundary to wall off. Optional, **opt-out** guards exist for teams that want them: a `ValidatingAdmissionPolicy` scoped to the customizer SA could protect specific supply-chain objects (the `cozystack-packages` OCIRepository) or reject changes to helm-controller-managed `Package` fields (detected via `managedFields`). These are **defense against accident, not prerequisites** — locking chart-owned `Package` fields by default would risk removing tuning knobs admins can reach today, so the direct-edit path stays open and `PackageValues` is the recommended non-racing path rather than a wall.
- **Tool 2 (tenant):** the surface runs as a dedicated **`<tenant>-gitops` SA** (write on `apps.cozystack.io` only) — *strictly narrower* than an interactive tenant user, so the git path can do strictly less than the tenant already can via the API — *once the routing is enforced*. The subtlety is that tenants hold **no** Flux-CRD RBAC today, so Tool 2 introduces the write path; the new risks are a tenant **altering or omitting the shard label** (routing to another tenant's controller, or falling through to the unrestricted main kustomize-controller) and **naming a foreign SA**. All are closed at admission by the per-tenant dedicated controller (`--no-cross-namespace-refs`, pinned SA), a fail-closed mutating webhook that stamps the label + `serviceAccountName` on create **and** update, and a `ValidatingAdmissionPolicy` that checks the shard-label **value** (not mere presence) on create **and** update. A distinct concern is the **source path**: tenant `GitRepository`s reconcile on the shared platform source-controller (SSRF/DoS surface) and artifacts are served unauthenticated over HTTP, so the dedicated kustomize-controller runs platform-side and tenant namespaces are denied egress to the artifact endpoint (see Tool 2 isolation point 4). This enforcement is a **prerequisite of the surface, not deferred hardening** — it must land in the same increment that grants tenants Kustomization write. Tenant-supplied input = the contents of a tenant-owned repo, bounded by the `<tenant>-gitops` SA.
- **Secrets:** both tiers may pull git credentials and SOPS keys. These are admin/tenant pre-created Secrets; the platform never generates or owns them.

## Failure and edge cases

- Missing `source.url` / `kustomization.path` when enabled → chart `fail`s at render; Flux surfaces the error on HelmRelease status (already implemented in the prototype).
- Private repo without a `secretRef` → GitRepository reports auth failure on its status; no partial apply.
- Admin manifest declares a chart-owned `Package` field directly (bypassing `PackageValues`) → the field is force-claimed from helm-controller, exactly as a hand-run `kubectl patch` does today. This stays available **by design** (it is an admin tool); teams that want it fenced can opt into the field-ownership VAP. `PackageValues` is the recommended non-racing path.
- Tenant Kustomization omits or **alters** the shard label (to reach another tenant's controller or the unrestricted main one), references a source in another namespace, or names a foreign SA — on create **or** a later update → the mutating webhook re-stamps the label + SA and the VAP rejects any shard-label value that is not the tenant's own, so it never reaches a foreign or unrestricted controller; the per-tenant controller's `--no-cross-namespace-refs` blocks the cross-namespace ref.
- **Uninstalling the tenant GitOps instance would cascade-delete the tenant's apps.** Deleting the instance deletes its `Kustomization`; with `prune: true` the finalizer garbage-collects everything it applied — the tenant's Application CRs, i.e. their databases and VMs. The tenant `Kustomization` therefore defaults to **`deletionPolicy: Orphan`** (the CRD supports it), so uninstalling GitOps leaves running apps in place; a tenant who wants cascading teardown opts into `Delete`/`MirrorPrune` explicitly.
- Customizer disabled then re-enabled → resources re-adopt cleanly (SSA, `resource-policy: keep`); no duplicate-creation errors.
- `PackageValues` for a non-existent Package → **not** a silent no-op; the operator has no Package to merge into and reports a status condition on the `PackageValues`, so a typo'd package name (or an override applied before its Package exists) surfaces loudly. Merge keys for absent *components* within an existing Package are ignored.
- A git-managed Application edited in the dashboard → the edit is refused (git-managed Applications are read-only in the UI) rather than silently reverted on the next reconcile.

## Testing

- **Helm unit tests** (`make unit-tests`): bundle wiring for the customizer (enabled / disabled / `disabledPackages`) — already in the prototype; add assertions for the RBAC surface (e.g. no `delete` on Packages). The surface is intentionally permissive on `cozy-system`, so if the optional supply-chain guard is enabled, assert it protects the `cozystack-packages` OCIRepository — it is opt-out, not a default `cozy-system` exclusion.
- **Operator unit tests** for `PackageValues` merge precedence on at least one downstream package (e.g. linstor): map keys merge deep, list keys follow the documented policy, and an absent `PackageValues` leaves `hr.Spec.Values` byte-identical to today.
- **Helm / webhook / policy unit tests** for the tenant `gitops` app: the rendered `Kustomization`(s) carry the tenant shard label and `spec.serviceAccountName: <tenant>-gitops`, `spec.deletionPolicy: Orphan`, and an in-namespace sourceRef; the mutating webhook stamps a label-less tenant Kustomization on create and re-stamps it on update; the VAP rejects one whose shard-label *value* is not the tenant's own (not merely a missing label) or that names a foreign SA, on both create and update.
- **e2e (BATS, `hack/e2e-apps/`):** enable the admin surface against a dev cluster, push a `PackageValues` change, assert the downstream HelmRelease re-renders and the effective values appear on `Package` status; create a tenant `gitops` app, assert it reconciles an Application CR, is denied a cross-namespace ref, and — the key isolation tests — assert a Kustomization created (or updated) to carry a foreign/absent shard-label value or a foreign `serviceAccountName` is rejected at admission (never reaching a foreign or unrestricted controller), that a tenant pod cannot reach the `flux.cozy-fluxcd` artifact endpoint, and that deleting the GitOps instance leaves the tenant's Application CRs running (`deletionPolicy: Orphan`).
- **Manual:** `kubectl auth can-i --as=...` matrix for both SAs (already done for the admin SA in #2731); verify the tenant SA cannot reconcile outside its namespace.

## Rollout

1. **Tool 1, Phase 1** — admin customizer system package + ownership-model docs ([#2731](https://github.com/cozystack/cozystack/pull/2731), in review). Ship the permissive default posture; the optional supply-chain / field-ownership guards (Security) are opt-in follow-ups.
2. **Tool 1, Phase 2** — the `PackageValues` admin override resource (new API kind + operator merge). Highest leverage: unblocks safe component-parameter changes (UC1b) and whole-cluster reproducibility (UC5).
3. **Tool 2, Phase 1** — tenant `gitops` app shipped **together with** its enforcement: the per-tenant dedicated kustomize-controller (platform-side) bound to a narrow `<tenant>-gitops` SA (a separate sharding path from the helm-controller pool), the fail-closed mutating webhook (label + `serviceAccountName`, create/update), the shard-value `ValidatingAdmissionPolicy` backstop, the source-path containment (artifact egress denied to tenants), and `deletionPolicy: Orphan` on the tenant Kustomization. This is not a later phase — granting tenants Kustomization write *without* it is the isolation hole, so they ship as one unit. The largest user-visible win.
4. **Cross-cutting** — secrets-as-code + dashboard sync-status views.
5. **Hardening** — field-ownership `ValidatingAdmissionPolicy` (separate proposal) and policy-as-code guardrails.

## Open questions

1. **Resolved (during review).** Management-plane tenant reconciliation is served by the **embedded flux-aio** (`internal/fluxinstall`), whose main controllers already carry `--watch-label-selector=!sharding.fluxcd.io/key`; the flux-operator-managed `FluxInstance` / `multitenant: false` in `packages/system/fluxcd` applies to **child Kubernetes clusters** only (migration 21 retired the FluxInstance path on the management cluster). Lockdown flags for the tenant surface therefore live on the **dedicated per-tenant kustomize-controller** the operator provisions (Tool 2), not on a global `FluxInstance`.
2. **Resolved (during review):** tenants stay confined to the curated **Application** abstraction. The surface grants the `source`/`kustomize` authoring needed to run reconciliation, but reconciliation itself runs as a dedicated **`<tenant>-gitops` SA scoped to write `apps.cozystack.io` only** (narrower than the interactive tenant SA, which also carries `sdn.cozystack.io/securitygroups` write). So a tenant's git repo can produce only Application CRs — "BYO from git" means Application CRs from git, not raw `HelmRelease`s or arbitrary CRs — and that bound is now **explicit RBAC on the git SA**, not an emergent property of `cozy:tenant:*`. This keeps the tenant trust model at-or-below today's; the surface only changes *how* Applications are submitted (git instead of dashboard/`kubectl`).
3. **`PackageValues` binding** — the merge precedence and list-vs-map policy are now fixed under *Admin override layer* (maps deep-merge, lists replace wholesale). The remaining question is binding: 1:1-by-name (as drafted) vs an explicit `spec.packageRef`. (The `packageOverrides` chart layer is dropped in favor of `PackageValues`, so it is no longer an open question.)
4. **Resolved (during review):** tenant GitOps is delivered as a **catalog app** — not a `Tenant` CRD field, and not a parent-gated extra-module singleton. It avoids core API surface, keeps the feature **self-service** and opt-in per tenant, and takes a **list of sources** in one instance so the natural shape is one instance ⇒ one dedicated controller per gitops-enabled tenant (no parent gate needed to bound the controller count; a `ResourceQuota` caps it where an admin wants a hard limit). Both the `spec.gitops` field and the extra-module singleton are recorded under Alternatives.
5. **Resolved (during review):** who reconciles tenant *sources*. Tenant `GitRepository`s land on the shared main source-controller in `cozy-fluxcd` (no source-controller sharding); rather than shard source-controller too, the surface runs the per-tenant kustomize-controller platform-side and denies tenant namespaces egress to the unauthenticated artifact endpoint, keeping the source path outside tenant reach (Tool 2 isolation point 4). A per-tenant source-controller remains a possible future step if the shared-source DoS/SSRF surface proves insufficient.

## Alternatives considered

- **Runtime mechanism — give admins/tenants raw Flux CRDs via RBAC, no chart.** Rejected: no curated trust tiers, no isolation defaults, and tenants would need source/kustomize RBAC plus a cross-namespace lockdown anyway. The primitive-as-a-package gives a consistent UX and safe defaults for the same underlying objects.
- **Field ownership — solve UC1b with RBAC (`resourceNames` allow/deny on Packages).** Rejected: RBAC is object-level, but on a single Package the contract is field-level (admin owns `spec.components.*.values`, chart owns `spec.variant` and structural fields), and RBAC cannot express that. Splitting overrides into a separate `PackageValues` resource turns the field-level contract back into an object-level one — write `PackageValues`, read-only `Package` — which RBAC *can* enforce. Denying the chart-owned Package object outright would instead re-introduce hand-management of the parameters admins most want.
- **Override layer — `packageOverrides` on the bootstrap Package (chart-only, no API change).** The override map would live under `spec.components.platform.values` and the platform chart would merge it into each downstream Package at render time. **Rejected/dropped:** it collapses all admin config onto one god-object with all-or-nothing RBAC, sidesteps the force-ownership race rather than removing it for the override path, and pushes list-vs-map merge correctness into per-package templates. `PackageValues` supersedes it as the single override mechanism.
- **Admin override — materialize `PackageValues` onto the `Package` via SSA (chart-wins).** Have the operator server-side-apply `PackageValues` content onto `Package.spec.components.*.values` under its own field manager, without `force`. This is genuinely *clean* on the current stack (helm-controller applies via SSA in Flux 2.8, and `values` is `x-kubernetes-preserve-unknown-fields`, so ownership is per-key): chart-owned and admin-owned keys coexist, a real overlap surfaces as a loud SSA conflict, deleting a `PackageValues` prunes the operator-owned keys and the Package reverts to chart state, and an orphaned `PackageValues` fails the apply. Its one benefit over the chosen design is that the `Package` becomes the single materialized source of truth. **Rejected as the default** because "no force" makes the **chart** win any genuine overlap, which pushes the admin back onto the racy direct-edit path for exactly the keys they meant to override; inverting that to "admin wins" *while* materializing would require `force` — the `ForceOwnership` race this proposal removes. Merging one layer below (`hr.Spec.Values`) keeps admin-authority **and** race-freedom; we recover the lost visibility by surfacing effective values on `Package` status. (If the project later prefers chart-authoritative overrides with loud conflicts over admin-authoritative silent ones, this becomes the recommended shape — the two differ only in who wins an overlap and where the merged result is read.)
- **Field ownership — bespoke admission webhook now.** Deferred: a `ValidatingAdmissionPolicy` (native, CEL, no webhook server) is the better shape *if* a team opts into fencing, and `PackageValues` already gives admins a non-racing path off the contested objects without walling the direct-edit path off.
- **Tenant isolation — flip global Flux multitenancy.** Rejected as the default: on the management plane it would constrain the platform's own Kustomizations (and the `FluxInstance` `multitenant` lever governs child clusters, not this plane anyway). A dedicated per-tenant kustomize-controller + SA, with a fail-closed webhook and a VAP backstop, confines the blast radius to the opted-in tenant and enforces isolation at admission.
- **Tenant isolation — reuse the helm-controller shard pool for Kustomizations.** Rejected: a shared, pooled kustomize-controller would need to impersonate every tenant's SA (helm-controller impersonates none, because tenants never author HelmReleases). A per-tenant controller keeps a 1:1 SA binding; opt-in keeps the count bounded.
- **Tenant delivery — extend the `Tenant` CRD with a `gitops` field.** Viable and more discoverable, but rejected: it adds core API surface and couples the feature to the `Tenant` lifecycle. The catalog app keeps it opt-in per tenant with no API change.
- **Tenant delivery — a parent-gated singleton extra module.** An extra module enabled by the *parent* tenant (as `etcd: true` today), one instance ⇒ one controller, with controller count under admin/parent control. Rejected: it trades tenant **self-service** for parent-gating and restriction-by-default, and the capacity concern it solves is already dissolved by letting the single catalog app take a **list of sources** (one instance, one controller per gitops-enabled tenant); a `ResourceQuota`/admission cap covers the rare hard-limit need without removing self-service. (A note on mechanism: `singularResource` in Cozystack is a *dashboard/UI* hint, not a provisioning switch, and no app sets it — it would not enforce a singleton, so the "extra module" would still be plain chart gating.)
- **One monolithic tool for all cases.** Rejected: admin and tenant surfaces have fundamentally different trust models, RBAC, and isolation requirements. They share a primitive, not an implementation.

---

<!--
Inspired by KubeVirt enhancement proposals
(https://github.com/kubevirt/enhancements) and Kubernetes Enhancement
Proposals (KEPs).
-->
