# Tenant-supplied secrets by reference, through External Secrets Operator

- **Title:** `Tenant-supplied secrets: referencing external secrets from application specs through External Secrets Operator`
- **Author(s):** `@lllamnyp`
- **Date:** `2026-09-21`
- **Status:** Draft

## Overview

A tenant who must hand a secret to a managed application has one option today: a plaintext field in the application spec. Tenants hold no verbs on `core/v1` Secrets, so the Helm convention of pointing at an existing Secret is unusable for them, and every feature that needed a tenant-supplied credential has copied the plaintext-in-spec shape: database user passwords, the vSphere account for VM import, the IPsec pre-shared key for site-to-site connectivity.

This proposal gives tenants a secret store of their own and lets application specs reference an entry in it by name. The store is reached through External Secrets Operator, installed by default and optional: an installation that disables it keeps everything else and loses secret references, the way an installation without KEDA loses database autoscaling. The batteries-included store is a single platform OpenBao with one OpenBao namespace per tenant, but the coupling between Cozystack and any store is confined to one per-tenant object, so a cluster operator or a tenant can point it at an external OpenBao, a cloud secrets manager, or the tenant's own managed OpenBao instead.

Platform-generated credentials, one-time disclosure and rotation are not in scope. They belong to the user secrets API proposal, and the boundary is stated below.

## Scope and related proposals

- **Absorbs [SecretRef for external credentials](https://github.com/cozystack/community/pull/37) (@IvanHunters).** Its analysis of how each engine consumes a credential and its rule that a chart passes a reference and never copies content are adopted. Its open question, how a tenant creates the referenced object, is what this proposal answers. That pull request should be closed as superseded, with its discussion kept.
- **Inherits [cozystack/cozystack#1942](https://github.com/cozystack/cozystack/issues/1942) (@lexfrei) as prior work to drive to completion.** That issue proposed OpenBao plus External Secrets Operator as the platform's secret architecture. Its storage half is this proposal. Its generation half, replacing render-time `lookup` plus random generation in charts, belongs to the user secrets API proposal and is not repeated here.
- **Sits beside [User secrets API and managed credential lifecycle](https://github.com/cozystack/community/pull/74) (@myasnikovdaniil).** That proposal owns credentials the platform generates: minting, disclosure, rotation, and the ownership handoff away from Helm. This proposal owns credentials the tenant brings. The boundary is the source of the bytes. Whether a deployment permits a supplied secret where the platform could generate one is a policy question that proposal decides. Its phase 2, a write-only `TenantSecret` version, is the alternative this proposal argues against under Alternatives considered; the maintainers' call proposed withdrawing it in favour of this proposal, and that comparison is what this pull request's review settles. Until this proposal's capability exists, that proposal's strict policy needs its own answer for a field that requires a supplied value.
- **Builds on the platform OpenBao work:** [cozystack/cozystack#2787](https://github.com/cozystack/cozystack/issues/2787), the central instance in [cozystack/cozystack#4177](https://github.com/cozystack/cozystack/pull/4177), and auto-unseal in [cozystack/cozystack#4168](https://github.com/cozystack/cozystack/pull/4168). The central instance that pull request introduces for tenant transit unseal is the instance this proposal uses as the default store.
- **Consumers waiting on it:** [Tenant site connectivity](../tenant-site-connectivity/README.md) left the pre-shared key as an open question; VM import materialises vSphere credentials from the spec with a code comment saying a tenant cannot create a Secret; the managed database charts accept passwords inline.
- **Adjacent, not overlapping:** [Unified TLS and PKI](../unified-tls-pki/README.md) delivers public trust anchors outward and evaluated External Secrets Operator for that job, rejecting it only because the operator was opt-in. This proposal installs it by default, which is not the always-on requirement that proposal names as the condition for retiring its extraction controller; that retirement stays its own decision.

## Decisions

<!-- Left empty in the initial PR. Records live under ./decisions/, numbered from 0001. -->

## Context

Baseline: cozystack at `37e22dc35`. Paths are in `cozystack/cozystack` unless stated.

**Tenants cannot touch `core/v1` Secrets.** The tenant ClusterRoles in `packages/system/cozystack-basics/templates/clusterroles.yaml` grant read on the aggregated `core.cozystack.io/tenantsecrets` view and no verb on raw Secrets. The view serves the whole data of every Secret the lineage webhook has labelled tenant-visible, which it decides from the owning application's `ApplicationDefinition` selectors. The reason for withholding raw access is reading, not writing: a tenant namespace holds operator superuser credentials, TLS private keys and backup credentials next to the tenant's own objects.

**The write path exists but is unused.** The registry in `pkg/registry/core/tenantsecret/rest.go` implements create, update and delete. No tenant role holds those verbs, and a write through it stamps the outward-projection label, so anything written becomes readable by the `use` tier.

**Every tenant-supplied secret is plaintext in a spec today.** User passwords on the database charts; the provider password on `VMImportSource`, which `internal/migrationcontroller/credentials_projector.go` copies into a Secret because, as its comment says, a tenant never creates one; the site-to-site pre-shared key, deferred for lack of anything better. Application specs are readable by every subject that can `get` the application, which the base tenant role grants to everyone in the tenant.

**External Secrets Operator is packaged but dormant.** `packages/system/external-secrets-operator` carries chart 0.10.4, well behind upstream, and renders only when listed in `bundles.enabledPackages`, which defaults to empty. Nothing in the tree creates an `ExternalSecret` or a `SecretStore`.

**OpenBao is arriving.** `packages/apps/openbao` is a managed app tenants can deploy. `packages/system/openbao` is a stub that never ran. The central platform instance, high-availability Raft over cert-manager TLS with an init Job and an unsealer, is in review as cozystack/cozystack#4177 and installs on every PaaS install. OpenBao namespaces, which partition one instance into isolated tenants with their own policies, auth methods and engines, shipped in OpenBao 2.3 and gained per-namespace sealing in mid-2026.

### The problem

- A tenant setting up site-to-site connectivity has to paste the peer's pre-shared key into the application spec, where every colleague with view access reads it.
- A tenant importing VMs from vSphere types the vSphere password into a spec field, and the platform copies it into a Secret the tenant cannot see, list or rotate.
- A tenant who keeps passwords in a company secret store has no way to make a managed database use one of them without pasting it into a spec.
- A chart author adding a credential-bearing field has no shared mechanism and reaches for the inline field, because it is the only one a tenant can fill.

## Goals

- A tenant can create, read, update and delete its own secrets without any grant on `core/v1` Secrets, and no other tenant can reach them.
- An application spec references a tenant secret by name; the value never enters the spec, the HelmRelease values, or a chart-rendered manifest.
- Every application that takes a tenant-supplied secret does so through the same mechanism.
- The store is pluggable at the platform level and at the tenant level, and the default store works on a fresh install with no external dependency.
- A tenant's Kubernetes clusters can consume the tenant's secrets with External Secrets Operator running inside them; for the default store this needs no configuration beyond enabling it.

### Non-goals

- Generating, disclosing once, or rotating platform-generated credentials. That is the user secrets API proposal.
- Credentials the platform holds for its own service accounts inside a managed application: the MariaDB root the operator applies at bootstrap, the ClickHouse backup user, the OpenSearch admin, Harbor's redis password. These are neither tenant-supplied nor tenant-facing, and today they share a Secret with tenant users and the grants on it. Separating them into Secrets the tenant is never granted is chart hygiene under the user secrets API proposal's closure of the raw-grant route, not this proposal.
- Delivering platform secrets outward to tenants. That is the existing `tenantsecrets` projection and the TLS proposal.
- Making External Secrets Operator or OpenBao a hard requirement. Both are default-installed optional packages; what an installation loses by disabling them is stated under Design, and it is expected that as applications convert credential fields to references, an installation without the capability can run fewer of them.
- Wrapping the store's own interface. A tenant creates and manages secrets through the store's API or UI, whichever store is configured; Cozystack provisions the tenant's identity and access in the default store and materialises referenced values, it does not proxy reads and writes. A dashboard convenience for the default store may follow and is not part of this proposal.
- Live rotation. A value changed in the store is re-synced into the cluster, but whether a running engine picks it up is engine-specific, tenants cannot restart engines, and no propagation guarantee is made.
- Dynamic database credentials, leases, or OpenBao secrets engines beyond key-value storage. The design permits them later; nothing here depends on them.
- Encryption at rest for etcd, or protection of the in-cluster store from a management-cluster administrator.

## Design

Five parts: the reference in the application spec, the seam between Cozystack and the store, the materialisation that makes a referenced value reachable by an operator, the default store behind the seam, and consumption from the tenant's own Kubernetes clusters. The first three are proposed as settled in principle; their concrete shapes are implementation choices for the conversion pull requests. The store topology is proposed with a recommendation and is the main open question.

### 1. The reference

An application field that carries a tenant-supplied secret takes a reference to an entry in the tenant's secret store rather than the value. The reference identifies the entry and, where the entry holds several values, which one. Its exact shape is not fixed by this proposal; the SecretRef proposal's `{name, key}` selector is one candidate and the conversion pull requests settle it.

Two rules do hold regardless of shape. A secret the platform cannot generate, such as a pre-shared key, a third-party token or a vSphere account, is reference-only: there is no inline form, and a required reference that is absent fails the render so the application reports not ready. A secret the platform can generate, such as a database user password, may accept either generation or a reference; whether a deployment permits the reference form for such a field is policy owned by the user secrets API proposal. References resolve in the tenant's own store only; there is no cross-tenant form.

### 2. The seam

Every tenant namespace carries one External Secrets Operator `SecretStore`, rendered by the tenant chart under a fixed name. Application charts refer to it by that name and nothing else. Where the store is, how it authenticates, and which provider it uses are properties of that one object, and that is the whole extent of Cozystack's coupling to any store. The tenant chart renders it only when the operator's API is present in the cluster, so disabling the package does not fail every tenant's release on an unknown kind.

The platform renders the default `SecretStore` pointing at the platform store described in part 4, authenticating as a dedicated per-tenant ServiceAccount the tenant chart also renders. Two overrides exist. A cluster-wide one, set by the operator, replaces the provider configuration every tenant's `SecretStore` renders, so an installation can point all tenants at an external OpenBao or a cloud secrets manager. A per-tenant one, set on the `Tenant` application, replaces it for that tenant alone, so a tenant can use its own managed OpenBao instance or a store outside the cluster it already operates. The constraint on the per-tenant override is that it can only make that tenant's own references fail.

Tenants get read access to the `SecretStore` and to the `ExternalSecret` objects in their namespace, so sync state is visible. They get no write access to either; the chart renders both.

### 3. Materialisation

Operators read `core/v1` Secrets by name. Something must put the referenced value into one, in the tenant namespace, without the tenant touching Secrets. The chart does it: for each reference in the spec it renders an `ExternalSecret` against the tenant's `SecretStore`, targeting a Secret whose name the chart chooses, and wires that Secret into the operator's native reference mechanism in whatever form the engine takes, following the SecretRef proposal's per-engine analysis. The chart never reads the referenced value at render time and never copies it into a chart-owned object.

Three consequences follow from the chart, rather than the tenant, rendering the `ExternalSecret`. Flux creates it with the platform's authority, so the tenant's only input is the reference. Lineage works: the `ExternalSecret` is a chart object tied to the application, and the materialised Secret is owned by it, so garbage collection follows the release; the `ApplicationDefinition` must not select these Secrets, so a supplied value is never projected outward through `tenantsecrets`. And the application's readiness can reflect the sync state of its references, since Flux can wait on the `ExternalSecret`.

One hazard the materialisation must account for: External Secrets Operator's default creation policy adopts a pre-existing Secret that has no owner, and every Helm-rendered Secret has no owner. The names the chart chooses for materialised Secrets must therefore be reserved and enforced at render time, so a reference can never overwrite an operator's or another template's Secret.

### 4. The default store

Behind the default `SecretStore` is the central OpenBao instance that cozystack/cozystack#4177 introduces, extended with a key-value namespace per tenant.

- **One OpenBao namespace per tenant**, created by the platform when the tenant is created and nested to mirror the tenant hierarchy, so a parent's administrators reach a child's namespace by the same nesting and a child cannot reach its parent or a sibling. Each namespace carries a key-value engine. Namespaces rather than path prefixes because they give a tenant delegated administration of its own space, and because per-namespace sealing lets a tenant withdraw the platform's access to its secrets later without a design change.
- **Machine access through Kubernetes auth.** The reconciler cozystack/cozystack#4177 adds for per-tenant transit keys is extended to provision, per tenant, an auth role bound to that tenant's dedicated ServiceAccount with read on that tenant's namespace. External Secrets Operator obtains a short-lived token per tenant and holds no long-lived credential.
- **Human access through the identity the installation already has.** With Keycloak, OpenBao's OIDC auth maps the existing tenant groups onto per-namespace policies and the audit device records the person. Without Keycloak, the tenant ServiceAccount token the tenant already holds authenticates through the same Kubernetes-auth mount; everyone in the tenant then writes as one identity and the audit records the tenant, not the person, which is the same limit the rest of the platform has without Keycloak. Tenants use OpenBao's API and UI as they are. Read-back of a secret the tenant wrote is permitted; it is the tenant's own value and the audit device is the control.
- **Bootstrap independence.** No platform component stores its own configuration or credentials in the store, and nothing in the platform's bootstrap depends on it. The store carries tenant-supplied values only. When it is unavailable, every already-materialised Secret stays in place and every running application keeps running; a new or changed reference stays pending until the store returns.

**Topology.** Three deployments of the store fit behind the same seam. The choice is open for review.

| Option | What it is | For | Against |
|---|---|---|---|
| A. In-cluster platform OpenBao, namespace per tenant | The instance from cozystack/cozystack#4177, on the management cluster | Works on a fresh bare-metal install with no external dependency; one instance to back up, upgrade and monitor; tenant hierarchy maps onto namespace nesting | Shamir keys sit in a Kubernetes Secret for the unsealer, so at-rest protection equals etcd's; a management-cluster compromise exposes every tenant's secrets; the store shares the cluster's fate |
| B. External OpenBao | The same layout on an OpenBao the operator runs elsewhere, reached through the cluster-wide override | The store survives and stays confidential across a management-cluster compromise or loss; KMS or transit seal available; no bootstrap coupling at all | An operator must run it; a fresh install has no store until they do; latency and reachability become the operator's problem |
| C. Per-tenant managed OpenBao | Each tenant deploys the `openbao` managed application and points its own `SecretStore` at it through the per-tenant override | Strongest isolation between tenants; no platform component holds every tenant's secrets; already a supported application | Three pods and a volume per tenant; unseal per instance, which is what the transit work in cozystack/cozystack#4177 exists to solve |

The proposal recommends **A as the default the platform installs, with B documented as the recommended production posture for deployments whose policy cannot accept the in-cluster root of trust, and C supported as a tenant-level choice rather than a platform topology.** The seam is what makes all three one design: the tenant chart renders a `SecretStore` either way, and no application chart knows the difference.

### 5. Consumption from tenant Kubernetes clusters

A tenant's workloads run in its managed Kubernetes clusters, and the secret a managed database consumes is usually one an application in that cluster needs too. The path is External Secrets Operator inside the tenant cluster with a store object there pointing at the tenant's secret store. Which store it is decides how much the platform has to do.

**Tenant-configured external store.** Nothing platform-side. The tenant installs External Secrets Operator in its cluster and configures it against the store with whatever authentication that store offers, as it would anywhere. The per-tenant override in part 2 and this configuration name the same store, so a secret written once is consumed by managed applications and by the tenant's own workloads alike.

**The default store.** Three things are not automatic, and the platform provides each:

- **Reachability.** Tenant egress is an explicit allow-list of platform namespaces in `packages/apps/tenant/templates/networkpolicy.yaml`, so the OpenBao namespace needs an allow rule of the kind Keycloak and the dashboard already have. The endpoint and the CA must be known inside the tenant cluster; cozystack/cozystack#4177 already publishes the central CA into tenant namespaces for transit unseal, and the same delivery carries it into the cluster.
- **Identity.** A tenant cluster's ServiceAccount tokens are signed by that cluster's own apiserver, so the platform's Kubernetes-auth mount from part 4 cannot validate them. The platform provisions, per tenant cluster, an OpenBao auth method that trusts that cluster's issuer. The Kamaji control plane runs on the management cluster, so OpenBao can reach it either for JWT auth against the cluster's OIDC discovery endpoint or for a Kubernetes-auth mount that calls the cluster's token review. JWT auth is preferred: no callback into the tenant cluster on every login and no reviewer permissions to grant on the tenant side, at the cost of the tenant's operator setting a bound audience on the tokens it requests. The role is bound to the tenant's OpenBao namespace with read policy; the tenant administers that namespace and can narrow it. The cluster's principal is distinct from the management-namespace ServiceAccount in part 4, so a compromised tenant cluster reaches only that tenant's secrets and nothing in the management cluster.
- **Wiring.** An `externalSecrets` addon on the managed Kubernetes application, beside cert-manager and the others under `addons`, installs External Secrets Operator into the cluster through the same admin-kubeconfig HelmRelease path the existing addons use, and renders a cluster-wide store object preconfigured with the endpoint, the CA, the auth mount and the tenant's OpenBao namespace. Enabling the addon is the whole configuration. A tenant that installs the operator itself finds the endpoint, CA and mount name on the cluster's application card and configures the store by hand.

Under option B the addon renders its store object from the cluster-wide override, and reachability from tenant clusters to the external store is the operator's responsibility. Under option C the tenant's own instance sits in the tenant's namespace on the tenant's network, and the addon points at it through the per-tenant override. Virtual machines that are not Kubernetes nodes are out of scope here; an agent-based path for them is an open question.

### 6. Capabilities and defaults

Neither component is a hard requirement. Both are installed by default and either can be disabled, and the proposal states what each disablement costs.

- **External Secrets Operator** becomes a default-installed package, bumped from the packaged 0.10.4 to a current release. Without it there is no `SecretStore` in any tenant namespace and no reference resolves. A chart that receives a reference on such an installation fails the render naming the missing capability, so the application reports not ready with a clear message rather than the release breaking; this is the behaviour database autoscaling already has when KEDA is absent. An application whose only credential field is reference-only cannot be installed there at all, exactly as an autoscaled database cannot.
- **The central OpenBao** from cozystack/cozystack#4177 becomes a default-installed package in the PaaS bundle, at one replica on single-node installs. Without it the default `SecretStore` has no provider, and an operator who disables it is expected to set the cluster-wide provider override to a store of their own; until they do, every reference reports not ready with the store missing. Nothing else in the platform notices, by the bootstrap-independence rule in part 4.

The trajectory is stated rather than implied: every application that converts a credential field to reference-only narrows what an installation without the capability can run. That is the intended direction, the default install ships the capability, and it is the same path the platform already took with Keycloak, which is optional and which managed Kubernetes and Grafana now integrate with out of the box.

## User-facing changes

- **Tenants** manage their secrets in the store's own interface; for the default store that is OpenBao's UI and API, reached with the identity they already have. Application forms gain a reference field beside every credential-bearing field. Existing inline fields keep working until each application deprecates them.
- **Tenant Kubernetes clusters** gain an `externalSecrets` addon that installs External Secrets Operator preconfigured against the tenant's store. The cluster's application card shows the endpoint, CA and auth mount for tenants who install the operator themselves.
- **Operators** get two platform values: the provider configuration for the default tenant `SecretStore`, and the OpenBao topology selection. A fresh install needs neither.
- **Chart authors** get one mechanism for tenant-supplied secrets and a rule: a new credential-bearing field is reference-only unless the platform generates the value.
- **`kubectl get <app> -o yaml`** shows a reference where it showed a password.

## Upgrade and rollback compatibility

- **Additive per application.** The reference field is optional and empty by default; existing inline fields render identically until a chart deprecates them, which each conversion pull request decides for its engine.
- **Two new default packages on upgrade.** External Secrets Operator and the central OpenBao install on the next platform upgrade unless the operator has disabled them. Neither touches existing tenant objects; the tenant chart renders the `SecretStore` and ServiceAccount on its next reconcile, and the OpenBao reconciler creates namespaces and roles for existing tenants.
- **Disabling a package after applications have converted** makes every reference in the cluster fail the render with the capability named; running applications keep their already-materialised Secrets until their next reconcile. This is a supported state with a stated cost, not a breakage.
- **Rollback of a converted application** to a chart without the field drops the `ExternalSecret` and its owned Secret. An application that was using a reference on a generatable field then falls back to generation, which is a credential change, not a no-op; the SecretRef proposal documented the same asymmetry.
- **Rollback of the platform components** leaves materialised Secrets in place unless the `ExternalSecret` objects are deleted first. The OpenBao data volume is retained on uninstall and must be part of platform backups from the first release that ships it.
- **Nothing here is irreversible** except deleting the OpenBao volume.

## Security

- **Trust boundaries added.** External Secrets Operator can create Secrets in every namespace; it has to be trusted at that level to exist at all, and Flux holds broader authority today. Under option A the central OpenBao holds every tenant's supplied secrets; isolation between tenants rests on OpenBao namespaces and per-tenant auth roles, and protection from a management-cluster administrator equals etcd's, as the topology table says.
- **Tenant isolation.** A tenant's `SecretStore` authenticates as that tenant's dedicated ServiceAccount, bound to that namespace and reaching only that tenant's OpenBao namespace. A tenant cannot name another tenant's store because the chart fixes the store name and the object lives in the tenant's namespace. The parent-tenant ServiceAccount chain that [cozystack/cozystack#4164](https://github.com/cozystack/cozystack/issues/4164) documents does not apply: the ServiceAccount is new and bound to nothing else.
- **Tenant-supplied inputs.** The reference is validated as a name. The per-tenant store override is provider configuration validated by External Secrets Operator's own schema, and can only make that tenant's references fail.
- **Read semantics inside a tenant.** A secret in the store is readable by whoever holds read policy on that tenant's namespace, mapped from tenant groups with Keycloak and from the tenant ServiceAccount without it. A supplied secret is never projected through `tenantsecrets`.
- **No new plaintext in chart output.** The `ExternalSecret` carries names only; the materialised Secret is a `core/v1` object the tenant cannot read, and its data is not in Helm history because Helm never renders it.
- **Name collision.** Materialised Secret names are reserved and checked at render time, because the operator would otherwise adopt an unowned Secret of the same name.
- **Audit.** The OpenBao audit device records every read and write with the acting identity for the default store. No audit claim is made for a store behind an override.

## Failure and edge cases

- External Secrets Operator not installed → a chart that receives a reference fails the render naming the capability; the tenant chart renders no `SecretStore`; nothing else changes.
- Default store disabled and no override set → the `SecretStore` has no provider; every reference reports not ready with the store missing until the operator sets the override.
- Reference names a store entry that does not exist → the `ExternalSecret` reports not ready with the provider's reason and the application reports not ready; it recovers on the next refresh after the entry appears. Nothing is generated in its place.
- Store unavailable → existing materialised Secrets and running applications are unaffected; new and changed references stay pending with the store error visible.
- Tenant deletes an entry an application still references → on the next refresh the `ExternalSecret` reports not ready; the materialised Secret keeps its last value, so the running application continues until restarted.
- Value changed in the store → re-synced into the Secret on the refresh interval; whether the engine picks it up depends on the engine and is documented per conversion; tenants cannot force a restart.
- Two applications reference the same entry → two materialised Secrets, one source.
- Per-tenant override fails to authenticate → every reference in that tenant goes not ready with the auth error; clearing the override restores the platform default.
- OpenBao namespace for a tenant missing because the reconciler has not run → the `SecretStore` reports not ready; the reconciler is idempotent and creates it on the next pass.
- Tenant deleted → the tenant chart's cleanup removes the `SecretStore` and ServiceAccount; the OpenBao reconciler removes the tenant's namespace and role on the tenant's finaliser. Under options B and C the platform removes only what it created.
- Tenant cluster cannot reach the store, or its auth method is missing → the operator inside the cluster reports its store not ready with the reason; workloads holding already-synced Secrets keep running. The auth method is provisioned on the cluster's finaliser like the namespace and removed with the cluster.

## Testing

- **Chart unit tests** on each converted chart: the operator's native reference points at the materialised Secret when a reference is set and at the generated or inline path otherwise; the referenced value appears nowhere in rendered output; a reserved-name collision fails the render.
- **Platform unit tests**: the tenant chart renders the `SecretStore` and ServiceAccount; the cluster-wide override replaces the provider configuration; the OpenBao reconciler creates and removes a namespace, engine, role and policy per tenant.
- **End to end**, on a live cluster with the central OpenBao and External Secrets Operator: a tenant writes a secret through OpenBao's API with its own identity and references it from a MariaDB user; the user authenticates with it; the application's rendered output contains no password; the tenant's ServiceAccount cannot read a sibling tenant's namespace; deleting the entry flips the application not ready. Repeat once with the per-tenant override pointing at a managed OpenBao application.
- **Tenant cluster consumption**, end to end: a managed Kubernetes cluster with the `externalSecrets` addon enabled syncs a secret from the tenant's store into a namespace of that cluster with no manual configuration; a second tenant's cluster, presenting its own tokens, is denied.
- **Negative lineage test**: the materialised Secret is never served through `tenantsecrets`.

## Rollout

The order is chosen so that nothing here waits on the central OpenBao. The seam takes any store, and a tenant's managed OpenBao application or an external store is enough to build and validate the first consumers against.

1. **This proposal accepted.** The SecretRef pull request is closed as superseded; cozystack/cozystack#1942 is relabelled to track the storage half here and the generation half in the user secrets API proposal.
2. **External Secrets Operator** bumped to a current release and made a default-installed package.
3. **The seam.** The tenant chart renders the `SecretStore` and ServiceAccount, gated on the operator's API being present; the cluster-wide and per-tenant overrides exist. With no default store yet, the provider comes from an override, which is how the first consumers are validated.
4. **First consumers**, settling the reference shape in the process: VM import, which has no generation fallback and is the clearest win; MariaDB, whose operator takes a reference natively; and the site-to-site gateway, which is waiting on it. Validated against a managed OpenBao application and against one external store.
5. **The default store.** The central OpenBao from cozystack/cozystack#4177 lands as a default-installed PaaS package; the reconciler provisions per-tenant namespaces, roles and policies; the default `SecretStore` provider points at it; tenant identities can authenticate to it.
6. **Remaining engines**, one pull request each, in the order the SecretRef proposal's engine analysis suggests. Each conversion decides its own inline-field deprecation.
7. **Tenant cluster consumption**: the per-cluster auth method in the OpenBao reconciler, the egress rule and CA delivery, and the `externalSecrets` addon on the managed Kubernetes application.

## Open questions

- **Topology default.** Option A is recommended as the installed default with B as the documented production posture. Reviewers who would rather install nothing in-cluster and require B, or who would make C the default, should say so; the seam supports any answer, but the answer decides what a fresh install ships.
- **The reference shape**, settled by the first conversions rather than here.
- **Schema of the per-tenant `secretStore` override** on the `Tenant` application, and which tenant tier may set it.
- **Read policy inside a tenant.** Whether `use`-tier members may read supplied secrets in the default store, or only administrators.
- **OpenBao namespace nesting** versus flat namespaces with policy-based parent access, pending a check of nesting limits on the pinned version.
- **Backup of the central OpenBao**: snapshot schedule and destination, before option A can be called production-ready.
- **Whether a dashboard convenience for the default store is wanted**, and if so whether it proxies OpenBao's API or embeds its UI.
- **Tenant cluster auth method**: JWT auth against the cluster's OIDC discovery, as recommended, or a Kubernetes-auth mount with token review; and whether one auth mount per cluster or one per tenant with per-cluster roles.
- **Virtual machine workloads**: whether an agent-based path from a VM to the tenant's store is wanted, and what identity a VM would present.

## Alternatives considered

- **A write-only `TenantSecret` API version**, the user secrets API proposal's phase 2. Its one advantage is real: no new components. Everything else counts against it. Tenant writes land in the namespace where operator superuser credentials, TLS keys and backup credentials also live, and RBAC cannot scope `create` by name, so a tenant can pre-create a name an operator creates-if-missing and choose its content, or pre-create a chart-rendered name and fail the release; `update` reaches every labelled Secret, which is exactly the set of application credentials, so a tenant can overwrite a password the database never received; `delete` removes a Secret a running workload mounts. The write-only shapes, private classification and supplier grants in that phase exist to fence those in, which is building a secrets engine inside an aggregated API on top of a store with no versioning, no read audit beyond the apiserver's, and optional encryption at rest. The store this proposal adopts has versioning, policy, audit and one-time wrapping built in, and the cost is an optional package. Rejected.
- **Granting the existing `tenantsecrets` write verbs and stopping there.** The cheapest form of the previous alternative, with every one of its hazards and none of its fences, plus every supplied secret projected to the `use` tier. Rejected, and not kept as a stopgap: the interim for a strict installation is the user secrets API proposal admitting the inline form as `Legacy` for reference-only classes until this capability exists.
- **A Cozystack controller that copies from the store into Secrets** instead of External Secrets Operator. Duplicates an operator that already handles every provider, refresh and ownership; the TLS proposal reached the same conclusion for its narrower case.
- **Tenants rendering `ExternalSecret` objects themselves**, with charts referencing the resulting Secret by name. Closer to plain External Secrets Operator usage, but it hands a tenant the ability to create a Secret of any name in its namespace, which is the adoption hazard in part 3, and moves the store reference out of the chart's control. Rejected; the chart renders the object.
- **A store with no platform integration**: run OpenBao, keep the inline fields, and let tenants consume secrets from their own workloads. This is what the managed OpenBao application already offers. It does not help a managed application consume a tenant secret, which is the problem.

---

<!-- Inspired by KubeVirt enhancement proposals and Kubernetes Enhancement Proposals (KEPs). -->
