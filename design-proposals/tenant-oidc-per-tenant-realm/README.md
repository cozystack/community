# OIDC for tenant Kubernetes clusters

- **Title:** `OIDC for tenant Kubernetes clusters`
- **Author(s):** `@IvanHunters` (revised with input from `@lllamnyp` and `@mattia-eleuteri`)
- **Date:** `2026-06-26` (revised `2026-06-30`)
- **Status:** Draft

## Overview

Tenant-owned Kubernetes clusters — the clusters tenants spin up via the `kubernetes` application — ship today with a single user-facing path: a static `cluster-admin` kubeconfig. This proposal gives them per-user OIDC: real identities, per-user audit, group/role-based RBAC, and access rotation by disabling a user instead of rotating a shared certificate.

The central decision is **what identity unit backs that OIDC**, and how much of it ships in the first cut. An earlier revision of this document answered "a dedicated Keycloak realm per tenant" and proposed implementing it directly. After the OIDC design syncs and review feedback from `@lllamnyp` and `@mattia-eleuteri`, the proposal is reorganized around a **phased** model, because per-tenant realm and "log a user into a cluster via OIDC" turned out to be two different-sized problems:

- **Phase 1 (ship now):** a per-cluster **issuer selector** — `oidc: System | CustomConfig | None` — plus a per-cluster public client for audience isolation, wired through a structured `AuthenticationConfiguration`. No new realm, no auto-provisioned Keycloak groups. This authenticates users that already exist in the platform `cozy` realm, and lets a tenant point a cluster at their own IdP. It is what the [`cozystack#3044`](https://github.com/cozystack/cozystack/pull/3044) implementation should land.
- **Phase 2 (deferred, may not be needed):** managed multi-tenant identity — letting a tenant own a directory the platform hosts. This is an IdP-as-a-service product and is scoped separately. Two candidate designs are compared below: **Keycloak Organizations** (lighter, recommended to evaluate first) and the **per-tenant realm** (the original design, retained here as the record).

The per-tenant-realm design is preserved in full under [Phase 2 — Option B](#option-b--per-tenant-realm-original-design-retained-as-record); it is no longer the Phase-1 baseline.

## Decision and phasing

| | Phase 1 (now) | Phase 2 (deferred) |
| --- | --- | --- |
| Identity source | platform `cozy` realm (`System`) or a tenant-provided issuer (`CustomConfig`) | a tenant-owned directory the platform hosts |
| New realm per tenant | no | only under Option B |
| Auto-provisioned Keycloak groups/clients lifecycle owned by the cluster | no | no (see [design principle 2](#2-couple-at-provisioning-decouple-at-ownership)) |
| Per-cluster audience isolation | yes | yes |
| Central-Keycloak load / billing impact | none beyond existing `cozy` | material — drives the Option A vs B choice |
| Needs tenant-hierarchy inheritance | no (issuer is global for `System`) | yes — needs an explicit owner marker |

The split is not arbitrary; it falls out of a few distinctions the earlier draft collapsed. Those are stated next, because they decide what belongs in Phase 1 and why Phase 2 may never be needed.

## Design principles

These four distinctions drive the rest of the proposal.

### 1. Two orthogonal tasks, and "BYO for *what*"

Treat every consumer of identity as choosing its own issuer: the Cozystack platform (console/API) is one consumer; each managed service (managed Kubernetes, and later managed Grafana, etc.) is another. `cozy` is simply the issuer Cozystack ships by default. Pointing a consumer at an *external* issuer is "BYO" — a **different task** from "use cozystack-provided identity," not a later phase of the same one. BYO then splits by target:

- **BYO for a managed service** — the service trusts the external IdP *directly*. A tenant's Okta can be the issuer for their managed Kubernetes with **no relationship to `cozy`** — `cozy` is never in the path. This is exactly EKS `associate-identity-provider-config`: the cluster apiserver trusts your IdP; your IdP never touches AWS IAM. In this proposal that is the `CustomConfig` selector.
- **BYO for Cozystack itself** — here, and only here, you *federate*: the external IdP is brokered into the platform identity so people sign into the Cozystack console with corporate credentials. Out of scope here; noted for completeness.

Org count (single-org vs multi-org) does **not** pick the model. Which *consumer*, and whether a *preexisting IdP should own it*, does.

### 2. Couple at provisioning, decouple at ownership

A cluster may **request** identity wiring — that is exactly what the `System | CustomConfig | None` selector is — but it must **not own the lifecycle of IdP objects**. The earlier draft's lifecycle showed the failure mode: `Tenant.spec.oidc=false` deletes the realm *and all its users*, so a directory gets destroyed by toggling a Kubernetes auth flag. And in the BYO case there is no business creating groups inside someone else's IdP at all. Phase 1 keeps the cluster owning only disposable, cluster-scoped objects (RoleBindings), never directory objects.

### 3. ops vs dev is authorization, not directory

The relevant split inside a tenant's workforce:

- **Infra admins / ops** — log into `cozy`, use the Cozystack API/console.
- **Developers** — need `kubectl` against their clusters but are *not* allowed into the cloud console.

Both are Phase-1 cases with no new realm. Ops are already in `cozy`. Developers either get a `cozy` identity that carries *no* console RBAC but a per-cluster `kubectl` grant (presence in the directory ≠ console access — authorization separates them), or the cluster trusts the company's own IdP directly (`CustomConfig`). A per-tenant realm would *fragment* one workforce across two platform-hosted directories, when the distinction is one of authorization.

### 4. The identity boundary is the organization, not every tenant

Cozystack tenancy is a tree — e.g. `tenant-root → organization → project`. The meaningful identity boundary is the **organization** (the customer-facing unit), not every nested tenant; projects should share their organization's identity rather than own a separate one. A per-tenant realm is the wrong granularity for a nested model — it forces solving "which tenant owns the realm" plus inheritance for every nested project. Any Phase-2 ownership marker therefore belongs at the organization level. (Raised by `@mattia-eleuteri`, who runs exactly this shape on the shared `cozy` realm + the `tenant-<name>-{view,use,admin,super-admin}` groups Cozystack already provisions.)

## Context

### The current shape

The `cozy` realm exists for management-cluster platform users. The dashboard and other platform components rely on it. Its per-tenant groups are `tenant-<name>-{view,use,admin,super-admin}` (rendered by `packages/apps/tenant/templates/keycloakgroups.yaml`, all bound to the `keycloakrealm-cozy` `ClusterKeycloakRealm`); they grant management-cluster RBAC and already map onto the tenant tree.

Tenant Kubernetes clusters provisioned by the `kubernetes` app (`packages/apps/kubernetes/`) ship with **one user-facing path**: the `kubernetes-<cluster>-admin-kubeconfig` Secret (the Kamaji-minted `super-admin.svc` kubeconfig). Anyone who needs `kubectl` takes the same `cluster-admin` cert — no per-user identity, audit, MFA, or group-based RBAC.

### Existing primitives (verified against `main`)

- **EDP Keycloak Operator** (`v1.edp.epam.com/v1alpha1`) — provides `ClusterKeycloak`, `ClusterKeycloakRealm`, `KeycloakClient`, `KeycloakClientScope`, `KeycloakRealmGroup`, `KeycloakRealmUser`, `KeycloakRealmIdentityProvider` (and more) as CRDs. Vendored under `packages/system/keycloak-operator/`.
- **`cozy` realm** — defined in `packages/system/keycloak-configure/templates/configure-kk.yaml` (`ClusterKeycloakRealm` `realmName: cozy`).
- **`apps/tenant` chart** — owns a tenant namespace; exposes a flat `Tenant.spec.*` API (today: `host`, `etcd`, `monitoring`, `ingress`, `gateway`, `seaweedfs`, `schedulingClass`, `resourceQuotas`). No `spec.oidc` yet.
- **`apps/kubernetes` chart** — renders `Cluster`, `KamajiControlPlane`, `KubevirtCluster`, MachineDeployments, addons. No `oidc` field today. Default tenant k8s version is `v1.35`, so the apiserver supports structured `AuthenticationConfiguration` (stable since 1.30).
- **`KamajiControlPlane`** — apiVersion `controlplane.cluster.x-k8s.io/v1alpha1` (the CAPI control-plane provider; not to be confused with the `tcp.kamaji.clastix.io` `TenantControlPlane` CRD). It accepts apiserver flags via `spec.apiServer.extraArgs` and supports `spec.deployment.extraVolumes` + `spec.apiServer.extraVolumeMounts`.
- **Namespace value propagation** — `packages/system/cozystack-basics/templates/cozystack-values-secret.yaml` emits a per-namespace `_namespace` bundle (`host`, `etcd`, `ingress`, `gateway`, `monitoring`, `seaweedfs`, `schedulingClass`). Inheritance today is a **single-level parent lookup** (`packages/apps/tenant/templates/namespace.yaml`); there is no recursive walk-up of the tenant tree.

## Goals

- A tenant cluster can authenticate `kubectl` users via OIDC: either against `cozy` (`System`) or a tenant-provided issuer (`CustomConfig`).
- Per-cluster token replay is impossible: a token minted for cluster A is rejected by cluster B, even within one tenant.
- Default RBAC is a **view + admin split, not blanket cluster-admin**, expressed declaratively. The static admin kubeconfig stays as break-glass.
- Authentication wiring is structured (`AuthenticationConfiguration`), so multi-issuer / BYO and private-CA paths extend the same Secret instead of fighting the chart shape.
- The chart API stays additive — clusters without OIDC render exactly as on `main`.
- No cluster owns the lifecycle of IdP directory objects ([principle 2](#2-couple-at-provisioning-decouple-at-ownership)).

### Non-goals

- **Managed multi-tenant identity / IdP-as-a-service.** That is Phase 2.
- **Federating an external IdP into the platform realm** ("BYO for Cozystack itself").
- **Custom credential plugin / RFC 8693 token exchange.** Possible future optimization; not required.
- **Cross-cluster SSO inside one tenant.** Each cluster has its own audience.

## Phase 1 — issuer selector + per-cluster client

This is what ships in [`cozystack#3044`](https://github.com/cozystack/cozystack/pull/3044).

### Consume-side API

`Kubernetes.spec.oidc` gains a selector (default `None`, fully backward-compatible):

```yaml
spec:
  oidc:
    mode: System          # System | CustomConfig | None
    # users: bind identities to cluster RBAC without creating any Keycloak group
    users:
    - email: alice@example.com
      role: admin         # admin | view
    - email: bob@example.com
      role: view
```

- `System` — trust the platform `cozy` realm via a per-cluster client (below).
- `CustomConfig` — trust a tenant-provided issuer directly (BYO for a managed service); the tenant supplies an `AuthenticationConfiguration` (inline or via `secretRef`). `cozy` is not in the path.
- `None` — no OIDC (today's behavior).

The enum is intentionally open for Phase 2: a future `KeycloakRealm` and/or `Organization` value selects a tenant-owned directory once Phase 2 lands — `oidc: System | CustomConfig | Organization | KeycloakRealm | None`.

### Layer A — `cozy` as the `System` default

`System` needs zero new identity infrastructure: `cozy` already exists and already holds the tenant's people. It is the natural zero-config default. The "no IdP of their own" shop — cited as the batteries-included case — *already has a directory* (`cozy`), because its people already log into Cozystack; the batteries-included win is "your Cozystack login works on your cluster," not "here is a fresh empty realm to populate."

### Layer B — per-cluster public client (audience binding)

For each `Kubernetes` CR with `oidc.mode: System`, `apps/kubernetes` renders, **in the `cozy` realm**:

- One `KeycloakClient` with `spec.clientId: <ns>-kubernetes-<cluster>`, `publicClient: true`, PKCE required, redirect URIs limited to `http://localhost:8000` / `http://localhost:18000` (kubelogin / oidc-login default ports).
- One `KeycloakClientScope` with an audience mapper so `id_token.aud` equals the clientId.

`clientId` is realm-wide unique (namespace-prefixed). The audience binding is the per-cluster isolation primitive: a token for `<ns-a>-kubernetes-prod` carries `aud: <ns-a>-kubernetes-prod`, and a different cluster's apiserver — configured for its own audience — rejects it. This per-cluster client is non-negotiable; a single shared client would let any tenant user mint a token usable against every cluster.

### Layer C — structured authentication config on the KamajiControlPlane

`apps/kubernetes/templates/cluster.yaml` wires the `KamajiControlPlane` to a structured `AuthenticationConfiguration` (`apiserver.config.k8s.io/v1beta1`) mounted as a file — not the legacy `--oidc-*` flags.

```yaml
spec:
  apiServer:
    extraArgs:
    - --authentication-config=/etc/kubernetes/authentication-config/config.yaml
    extraVolumeMounts:
    - name: authentication-config
      mountPath: /etc/kubernetes/authentication-config
      readOnly: true
  deployment:
    extraVolumes:
    - name: authentication-config
      secret:
        secretName: <release>-oidc-authn-config
        items:
        - key: config.yaml
          path: config.yaml
```

The Secret `<release>-oidc-authn-config` carries:

```yaml
apiVersion: apiserver.config.k8s.io/v1beta1
kind: AuthenticationConfiguration
jwt:
- issuer:
    url: https://keycloak.<root-host>/realms/cozy     # System; or the tenant issuer for CustomConfig
    audiences:
    - <ns>-kubernetes-<cluster>                        # equals the per-cluster clientId
  claimMappings:
    username:
      claim: preferred_username
      prefix: ""
    groups:
      claim: groups
      prefix: ""
```

Why structured config rather than flags:

- **Multi-issuer ready.** `jwt` is a list — `CustomConfig` (and a future Phase-2 issuer) add entries without rewiring the chart.
- **Private-CA support.** `issuer.certificateAuthority` accepts inline PEM, so a tenant self-signed issuer (common on-prem) works. The flag path cannot.
- **Single point of change.** Future claim mappings / CEL extend one file.

### Layer D — RBAC without auto-provisioned groups

Phase 1 does **not** create Keycloak groups. The `users:` map binds identities directly to cluster RBAC. A bootstrap Job (Helm `post-install,post-upgrade` hook; ServiceAccount scoped to the tenant namespace; pod labeled `policy.cozystack.io/allow-to-apiserver: "true"`, matching the existing cluster-autoscaler / kccm / CSI-controller egress pattern) applies, inside the tenant cluster, one `ClusterRoleBinding` per listed user:

- `role: admin` → `ClusterRole/cluster-admin`, subject `User: <issuer-username>`.
- `role: view` → `ClusterRole/view`, subject `User: <issuer-username>`.

These bindings are disposable, cluster-scoped objects — the cluster owns them, never any directory object ([principle 2](#2-couple-at-provisioning-decouple-at-ownership)). This also avoids creating groups inside a tenant's own IdP in the `CustomConfig` case, and avoids any collision with the existing `tenant-<name>-*` groups in `cozy`. A tenant wanting finer-grained roles or group-based binding authors them in their own GitOps and binds to the `groups:` claim themselves; nothing in Phase 1 blocks that.

The static admin kubeconfig stays as the documented break-glass path.

### Layer E — OIDC kubeconfig Secret

The same Job writes a `kubernetes-<cluster>-oidc-kubeconfig` Secret in the management tenant namespace (issuer-url, client-id, server-url, and a ready `kubeconfig` with a `kubectl oidc-login` exec block). `packages/system/kubernetes-rd/cozyrds/kubernetes.yaml` already lists `kubernetes-<cluster>-admin-kubeconfig` under `spec.secrets.include[].resourceNames`; this adds the OIDC Secret name alongside it so the dashboard exposes it to tenant viewers.

### Note on propagation

Phase 1 needs **no** tenant-hierarchy walk: with `System` the issuer is the single global `cozy`, and `CustomConfig` is explicit per cluster. The recursive realm inheritance the earlier draft described does not exist today (propagation is single-level — see Context) and is **not** introduced in Phase 1. It becomes relevant only in Phase 2, where it should use an explicit owner marker rather than walk-up depth (see below).

## Phase 2 — managed multi-tenant identity (deferred)

Letting a tenant own a directory the platform provisions, hosts, and that tenants self-administer is **IdP-as-a-service** — an entire product category (Cognito, Auth0, WorkOS exist solely for this). It is neither of the Phase-1 tasks, it loads the central platform Keycloak, and it raises a billing question ("how is a tenant's directory metered?"). It must be scoped and designed on its own, not as a side effect of the kube-apiserver OIDC PR. It may also never be needed: per principles 1–4, the ops/dev populations are already served in Phase 1.

> **This section is not a finalized design.** It maps the problem space and records the candidate options; it does not pick one. Phase 2 will require its own design proposal with a dedicated review round and explicit decisions — at minimum: the issuer/directory ownership model, billing/metering for hosted identity, multi-level tenant inheritance, and the security-boundary trade-off (per-realm isolation vs. Organizations-as-membership) — before any implementation begins. The material below is input to that round, not its conclusion.

If/when it is taken up, two designs are on the table.

### Option A — Keycloak Organizations (evaluate first)

Keycloak Organizations (a per-realm feature; being exposed upstream in [`cozystack/cozystack#3031`](https://github.com/cozystack/cozystack/pull/3031)):

- One shared `cozy` realm; one **Organization per organization-tenant**.
- Per-org domains, membership, and **per-org IdP brokering** — which is the BYO-for-Cozystack story scoped to the customer rather than per-cluster.
- Honors "decouple at ownership" naturally: the platform owns the realm; the customer owns their Organization (members + brokered IdP). No realm proliferation, no per-tenant Keycloak hosting, no central-Keycloak realm explosion.

Caveat (from `@mattia-eleuteri`): Organizations are membership + brokering + attributes, **not** a hard security boundary — clients and roles stay realm-level. So Organizations *complement* the per-cluster client + RBAC model rather than replacing it. For the nested `organization → project` shape this is the right granularity, and it is markedly lighter than a realm per tenant.

### Option B — per-tenant realm (original design, retained as record)

The original proposal. Kept here in full because it is the strongest *isolation* answer (realm-level config isolation, per-realm auth flows) if a tenant genuinely needs that — at the cost of central-Keycloak load and lifecycle/granularity complexity.

- **Realm.** `Tenant.spec.oidc=true` renders a sub-HR (`packages/extra/oidc/`, new in `cozystack#3044`) owning one `ClusterKeycloakRealm` `tenant-<ns>` wired to the platform master `ClusterKeycloak`, plus a bootstrap `KeycloakRealmUser`.
- **Per-cluster client + audience** inside `tenant-<ns>` (as in Phase 1 Layer B, but in the tenant realm).
- **Groups.** Two `KeycloakRealmGroup`s per cluster (`-view`, `-admin`) mapped to `ClusterRole/view` and `ClusterRole/cluster-admin` inside the tenant cluster.
- **Propagation.** A realm hint flows through the namespace-values channel.

Known costs, which are why this is no longer the default:

- **Central-Keycloak load / billing** — N realms vs. 1. This is the load concern; it needs a billing model. A mitigation discussed on the syncs is to run Keycloak as an *External App inside the tenant namespace* (billing becomes trivial — it is just API + DB pods), at the cost of nested-tenant wiring.
- **Lifecycle coupling** — provisioning the realm from `Tenant.spec.oidc` means a Kubernetes toggle owns a directory's lifecycle (violates [principle 2](#2-couple-at-provisioning-decouple-at-ownership)).
- **Granularity** — a realm per *tenant* is finer than the organization boundary ([principle 4](#4-the-identity-boundary-is-the-organization-not-every-tenant)); it forces "which tenant owns the realm" + inheritance for every nested project.

### How to choose, and the inheritance marker

Pick by the actual driver. If the driver is **per-customer identity + BYO IdP** (the common case), Option A covers it at a fraction of the operational cost. Only reach for Option B if a tenant needs realm-level config isolation that Organizations cannot give. Either way, the owner of the Organization/realm should be an **explicit marker on the owning (organization-level) tenant**, with inheritance resolving to that ancestor — not a fragile single-/multi-level walk-up of the tree (`@mattia-eleuteri`).

## Security

- **Trust domains stay separate.** Phase 1 `System` reuses `cozy` but binds tenant-cluster access only through per-cluster clients + cluster-local RoleBindings; no `tenant-<name>-*` `cozy` group is referenced from a tenant *cluster* binding, and no tenant-cluster identity is referenced from a management-cluster binding.
- **Per-cluster audience binding.** A token for `aud: <ns>-kubernetes-A` is rejected by cluster B. No webhook, no extra moving parts.
- **`system:authenticated` exposure.** Every OIDC-authenticated user lands in `system:authenticated` with the kube-default discovery rights — true for *any* OIDC integration. The per-cluster audience binding makes it safe: a token only reaches the cluster it was issued for.
- **`CustomConfig` trust.** With BYO, the cluster trusts the tenant's issuer directly; `cozy` is not in the path, so a tenant IdP compromise is contained to that tenant's own cluster(s).
- **Bootstrap Job ServiceAccount.** Scoped to its tenant namespace + write access to the specific OIDC-kubeconfig Secret; Cilium egress gated by `policy.cozystack.io/allow-to-apiserver`.

## Failure and edge cases

- **`oidc.mode: System` but the platform OIDC prerequisite is missing** → chart `fail`s the render with a clear message.
- **`CustomConfig` with an unreachable/invalid issuer** → apiserver rejects tokens; admin kubeconfig keeps working (break-glass).
- **Bootstrap Job egress blocked by Cilium** → Job pod carries `policy.cozystack.io/allow-to-apiserver: "true"`; without it the apply times out and the Job fails (surfaced in HR status).
- **`kubectl` without the `oidc-login` plugin** → clear client-side error; documented prerequisite next to the kubeconfig Secret.
- **Deeper tenant nesting (Phase 2)** → resolved via the explicit owner marker, not walk-up depth.

## Testing

- **helm-unittest** for `apps/kubernetes`: `oidc.mode: None` baseline (zero new objects, zero extraArgs); `System` (per-cluster `KeycloakClient` + scope, authn-config Secret, KCP extraArgs/volumes, one CRB per `users` entry); `CustomConfig` (authn-config Secret from supplied config, no Keycloak objects in `cozy`).
- **bats e2e** under `hack/e2e-apps/`:
  - `kubernetes-oidc-system.bats`: `oidc.mode: System` with a `users` map; verify client+scope land in `cozy`, KCP gets `--authentication-config`, the right CRBs land in the tenant cluster, the oidc-kubeconfig Secret exists; `view` user is read-only, `admin` user is cluster-admin; teardown removes the CRBs + Secret.
  - `kubernetes-oidc-customconfig.bats`: `oidc.mode: CustomConfig` with a BYO issuer; verify the apiserver trusts it and nothing is created in `cozy`.
- **Manual e2e:** drive both modes end-to-end on a dev cluster.

(Phase-2 testing is scoped with the Phase-2 proposal.)

## Rollout

1. **Phase 1** ([`#3044`](https://github.com/cozystack/cozystack/pull/3044)): `Kubernetes.spec.oidc` selector (`System | CustomConfig | None`, default `None`), per-cluster client, structured authn-config, `users`-based RBAC, OIDC kubeconfig Secret, dashboard exposure. No existing cluster is affected.
2. **Phase 2** (separate proposal, if needed): managed multi-tenant identity — evaluate Option A (Keycloak Organizations) before Option B (per-tenant realm), driven by whether the requirement is per-customer IdP brokering or realm-level isolation.

## Open questions

1. **`CustomConfig` API shape** — inline `AuthenticationConfiguration` vs. `secretRef` vs. both; how a BYO audience composes with (or replaces) the per-cluster audience.
2. **Phase 2 trigger** — what concrete tenant requirement would justify Phase 2 at all, and does Option A satisfy it without Option B.
3. **`cozystack` CLI** — a `cozystack kubeconfig --oidc <cluster>` helper that materializes the OIDC kubeconfig + prints `kubectl oidc-login setup`. Likely yes, follow-up.

## Alternatives considered

**Per-tenant realm as the Phase-1 baseline (the earlier version of this doc).** Reframed, not discarded — see [Phase 2 — Option B](#option-b--per-tenant-realm-original-design-retained-as-record). It is an IdP-as-a-service product (central-Keycloak load, billing, lifecycle coupling, organization-vs-tenant granularity), so it does not belong in the kube-apiserver OIDC cut. The earlier draft rejected the flat-`cozy` + per-cluster-client option on the grounds that it "forces tenant end-users into the platform realm" — but `System` does not: it authenticates the people already in `cozy`, and `CustomConfig` covers everyone who should come from an external IdP.

**Keycloak Organizations.** Now a first-class Phase-2 option (Option A), not a rejected alternative.

**BYO-OIDC as the only path.** Rejected as the *only* path: many tenants have no IdP and need a zero-config default — `System` is that default. BYO is the `CustomConfig` selector, shipped in Phase 1 alongside it.

**EKS-style flat IAM + webhook.** Rejected: a central webhook on every cluster's auth hot path is an HA-critical per-cluster service; per-cluster audience binding gets the isolation without operating it. (EKS's direct-trust `associate-identity-provider-config` is, by contrast, exactly the `CustomConfig` model.)

**RFC 8693 Token Exchange via a custom exec plugin.** Deferred: cleaner "single login, many clusters," but depends on maintaining a `client.authentication.k8s.io` plugin. The per-cluster client + audience model works today with stock `kubectl oidc-login`.

**Embedding the OIDC kubeconfig in the existing admin-kubeconfig Secret.** Rejected: that Secret is the platform's cluster-admin cert path (consumed by kccm, CSI controller, cluster-autoscaler); a separate `kubernetes-<cluster>-oidc-kubeconfig` keeps the user-facing surface distinct and gives the dashboard a clean object to expose.

**Single global `kubernetes` Keycloak client shared by every cluster.** Rejected: defeats per-cluster audience isolation; every user could mint a token usable against every cluster.

## Acknowledgements

This revision folds in the OIDC design-sync decisions and review feedback from `@lllamnyp` (the Phase-1/Phase-2 split, the "BYO for what" taxonomy, and "couple at provisioning, decouple at ownership") and `@mattia-eleuteri` (the organization-level identity boundary, Keycloak Organizations as the lighter Phase-2 path, and the explicit-owner inheritance marker).
