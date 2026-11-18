# Bank-Vaults integration for managed OpenBao

- **Title:** `Optional Bank-Vaults integration for OpenBao lifecycle, injection and reload`
- **Author(s):** `@xbrekz1`

## Overview

Thank you to the Cozystack maintainers and contributors for bringing managed services,
Kubernetes and GitOps together in one platform. We propose packaging a Bank-Vaults /
OpenBao configuration used in our deployment so other users can adopt the same
workflow without maintaining all of the integration manifests themselves.

The solution has three independent parts: an optional server backend managed by
Vault Operator, workload secret injection, and experimental opt-in secret reload.
The operational reference exists; the Cozystack API/package changes proposed here
have not been implemented. The examples are anonymized and have explicit prerequisites.

Our OpenBao compatibility fix in [Secret Reloader #702](https://github.com/bank-vaults/vault-secrets-reloader/pull/702)
was merged and included in [v0.5.1](https://github.com/bank-vaults/vault-secrets-reloader/releases/tag/v0.5.1).
It resolves namespace-related authentication and health-check failures without a
private fork. The scope and upstream maturity limits are documented below.

## Scope and related proposals

This builds on [cozystack#2787](https://github.com/cozystack/cozystack/issues/2787).
It complements [native self-init](https://github.com/cozystack/community/pull/134),
[central Transit](https://github.com/cozystack/cozystack/pull/4177) and
[the ESO credential contract](https://github.com/cozystack/cozystack/issues/1942).
Injection can ship independently of the server backend and work with an existing
managed or external OpenBao. No existing installation changes by default.

## Decisions

<!-- Add decision records here as implementation proceeds. -->

## Context

The implementation review below is pinned to Cozystack revision
`d88322197f75dd30ec51502cad1d58a722bc46ee`.

The existing path is:

OpenBAO application → application chart → inner HelmRelease → upstream OpenBao chart
→ StatefulSet, Services and PVCs.

Source references pinned to the reviewed revision:

- [ApplicationDefinition and public schema](https://github.com/cozystack/cozystack/blob/d88322197f75dd30ec51502cad1d58a722bc46ee/packages/system/openbao-rd/cozyrds/openbao.yaml).
- [Application template](https://github.com/cozystack/cozystack/blob/d88322197f75dd30ec51502cad1d58a722bc46ee/packages/apps/openbao/templates/openbao.yaml).
- [Public values](https://github.com/cozystack/cozystack/blob/d88322197f75dd30ec51502cad1d58a722bc46ee/packages/apps/openbao/values.yaml).
- [PackageSource](https://github.com/cozystack/cozystack/blob/d88322197f75dd30ec51502cad1d58a722bc46ee/packages/core/platform/sources/openbao-application.yaml).

The PackageSource installs openbao-rd and publishes app/system artifacts. The system
chart component has no install section; it is not a running central OpenBao instance.
The app renders an inner Flux HelmRelease named <release>-system with an ExternalArtifact
chartRef and valuesFrom cozystack-values.

At this revision the app disables listener TLS and injector/CSI, supports Shamir and
static seal, uses file storage at one replica and Raft above one, and sets the
StatefulSet PVC policy to whenDeleted: Delete / whenScaled: Retain.
Do not infer deployment state from the name packages/system/openbao.
See [the current app example](./examples/current-app.yaml) for the existing tenant API.

| Work | State observed | Implication for this proposal |
| --- | --- | --- |
| [#2059](https://github.com/cozystack/cozystack/pull/2059) | Initial managed-app implementation | Discussion explicitly distinguishes user app from platform service; TLS was deferred to a common approach. |
| [#2787](https://github.com/cozystack/cozystack/issues/2787) | Open; design doc requested | Closest parent discussion. Some original gaps are now partially addressed. |
| [#4766](https://github.com/cozystack/cozystack/pull/4766) | Merged | Static auto-unseal already exists; preserve its explicit migration guards. |
| [community#134](https://github.com/cozystack/community/pull/134) | Open proposal, updated Nov 17 | Native initialize with static seal, bootstrap initially limited to one replica; avoids exporting root token. Not a shipped feature. |
| [#4177](https://github.com/cozystack/cozystack/pull/4177) | Open draft, stale-labelled | Central Raft/TLS instance and per-tenant Transit provisioning; do not describe it as available. |
| [#1942](https://github.com/cozystack/cozystack/issues/1942) | Open discussion | Shared credential contract and optional OpenBao/ESO; direct injection should coexist with it. |
| [community#82](https://github.com/cozystack/community/pull/82) | Related credential-contract proposal | Reference-based credential input needs agreement; do not assume tenants may create Secrets today. |
| [#2483](https://github.com/cozystack/cozystack/issues/2483) | Open | Tenant init/unseal UX and permissions must be tested through the public API. |
| [#4061](https://github.com/cozystack/cozystack/issues/4061) | Open report | TLS certificate renewal needs an actual reload/restart acceptance test. |
| [#4229](https://github.com/cozystack/cozystack/issues/4229) | Open request | Render native declarative audit config; do not enable unsafe API audit creation as a shortcut. |

The integration should build on #2787 and coordinate with community#134 rather than
duplicate the existing auto-unseal work.

### The problem

Users who need external unseal, declarative OpenBao configuration and direct secret
injection currently have to assemble multiple controllers, identities and policies.
That creates repeated integration work and unclear upgrade/recovery ownership.
A supported, optional configuration contract could reduce this work while preserving
Cozystack's existing deployment paths.

## Goals

- Provide direct injection using a scoped application identity and verified TLS.
- Allow an existing managed/external server without requiring Vault Operator.
- Offer an optional operator backend with explicit ownership and recovery rules.
- Support separately enabled KV v2 reload with scoped read/patch permissions.
- Pin and test the complete component combination, including failure and restore cases.

### Non-goals

- Making OpenBao or Bank-Vaults mandatory, or replacing ESO.
- Automatic conversion of existing Helm instances or a file-to-Raft migration.
- Advertising HA or general OpenBao support based on a one-replica reference.
- Treating experimental Reloader as a default production controller.

## Design

The [examples guide](./examples/README.md) maps each configuration to its owner and
prerequisites. Real existing APIs and proposed Cozystack fields are kept separate.


### Components

| Component | Capability | Proposed scope |
| --- | --- | --- |
| OpenBao | Encrypted storage, auth, ACLs, namespaces, KV, PKI, Transit, dynamic credentials and Raft snapshot API | Remains the secret server. Engines such as database/PKI are OpenBao features, not new features implemented by Bank-Vaults. |
| Vault Operator | Reconciles Vault CR into server workloads and related resources | Optional platform-managed controller; tested OpenBao command/image configuration. |
| Bank-Vaults CLI / unsealer | Initialization, external key storage, repeated unseal; multiple backend choices | Bootstrap separated from read-only runtime; no circular dependency. |
| Bank-Vaults configurer | Declarative Kubernetes/JWT/OIDC auth, roles, policies, engines and supported identity/settings | Platform-controlled configuration; namespace-targeted reconciliation can use a separate configurer. Root-token/privilege handling remains an explicit design concern. |
| Bank-Vaults secrets webhook | Pod mutation and secret references; supports environment and additional templating modes | Initial supported profile: direct environment injection, selected namespaces and Pods only. Secret/ConfigMap mutation disabled. |
| vault-env / newer secret-init tooling | Resolve references and start the application; some modes support leases/templates | Pin one tested toolchain. File/agent/daemon modes require separate lease and application-reload tests. |
| Vault Secrets Reloader | Detect KV v2 version changes and trigger selected workload rollouts | Experimental, independently enabled. Not Stakater Reloader and not credential rotation. |
| ESO | Synchronize/generated credentials into Kubernetes Secrets | Existing complementary consumption path; no forced replacement. |
| cert-manager / platform TLS | Issue and renew certificates and distribute trust | Certificate creation does not prove that a running OpenBao reloads it. |
| Snapshot/monitoring integration | Scheduled snapshots, off-site storage, restore, sealed/stale-backup alerts | Reuse Cozystack primitives. Bank-Vaults unseal is not a backup scheduler. |

Bank-Vaults also documents plugins, startup-secret configuration, templates, external
encryption/KMS/HSM integrations, metrics and Velero backup integration. These are
extension points, not all required in the first release. Startup secret values must
not be embedded in public application values. Version-specific OpenBao compatibility
must be checked for every enabled feature, especially audit and namespace APIs.

Sources: [Bank-Vaults configuration](https://bank-vaults.dev/docs/concepts/external-configuration/),
[operator API](https://bank-vaults.dev/docs/operator/reference/),
[webhook](https://bank-vaults.dev/docs/mutating-webhook/),
[backup integration](https://bank-vaults.dev/docs/operator/backup/).

### Placement

Management Kubernetes cluster:
  Cozystack OpenBAO app → optional Bank-Vaults backend → Vault CR
  platform Vault Operator → OpenBao + unsealer + optional configurer
  platform TLS / network policy / monitoring / snapshot jobs

Workload Kubernetes cluster (same cluster OR a separate tenant cluster):
  selected Pod admission → Bank-Vaults webhook → runtime secret resolver
  application ServiceAccount → OpenBao auth → allowed KV path → process environment
  optional Reloader → version check → patch Pod template → new Pod → fresh injection

Independent external key store (optional):
  primary unsealer ServiceAccount JWT → external OpenBao root JWT auth
  → selected child-namespace KV shares → primary local /sys/unseal

The webhook and Reloader run against the workload cluster API. Install one scoped
webhook per cluster, not a cluster-wide webhook for every OpenBao application.
The server can stay in the management cluster or be external. Reloader 0.5.1 has one
configured server/namespace per process; separate trust domains require separately
scoped instances or a later multi-instance design, not one root-wide reader.

Prefer independent opt-ins:
1. Client integration with the existing managed/external OpenBao.
2. Experimental reload for explicitly opted-in workloads.
3. Server backend only where operator lifecycle/configuration offers enough benefit.

### API and package integration

[proposed-app.yaml.example](./examples/proposed-app.yaml.example) sketches NEW Cozystack fields; they are not supported today.
[vault-cr-example.yaml](./examples/vault-cr-example.yaml) shows the real operator API separately, including Raft, TLS,
declarative audit and external unseal. It is a reference with prerequisites,
not a renderer for the proposed API.
Keep the OpenBAO resource kind; default backend remains helm.
Initially make backend immutable after creation. New bankVaults instances use Raft
at one replica too. Start with a one-replica supported profile; gate HA behind tests.

Implement proposed packages for operator and workload integration using the existing
PackageSource/component mechanism. Exact package names remain a maintainer decision.
The operator is installed once with constrained watch/RBAC scope; raw Vault CR writes
are restricted to platform automation. Guest webhook/reloader installation uses the
existing guest-addon deployment path, including readiness dependencies.

Changes would touch:

- packages/apps/openbao: validated values and backend-specific rendering.
- packages/system/openbao-rd and generated API/types: public schema/UI parity.
- New operator/injection package definitions and platform dependency wiring.
- Kubernetes guest-addon configuration for in-guest webhook/reloader placement.
- WorkloadMonitor/resource-map/lineage/dashboard references: resolve operator-created resources.
- Network policy: API access, DNS, OpenBao TLS endpoint, external key store and backup S3.
- Tests: Helm unit tests, API generation and Chainsaw acceptance tests.

Reject arbitrary template strings, cross-tenant Secret references, unapproved server
endpoints and unrestricted Pod customization in the tenant API. Cozystack rendering
runs with privileged controllers; importing the entire Vault CR as values would
create a much broader security surface than the current app.

OIDC/Keycloak login is a separately configured human-auth flow; it is not the
ServiceAccount authentication used for injection or unseal. Each OpenBao namespace
needs deliberate auth/policy ownership; neither a Kubernetes namespace name nor a KV
path creates an OpenBao namespace automatically.

The operator should own its generated StatefulSet and Services. The platform owns
its operator installation, TLS resources and approved configuration. Flux owns the
Vault CR. A distinct controller/Job owns snapshots. Do not let Helm and the operator
simultaneously own the same workload. CRD upgrades must precede new CR fields.

### Injection and reload

[workload.yaml](./examples/workload.yaml) shows the real annotations for the pinned legacy vault-secrets-webhook
1.23.1 / vault-env 1.23.0 profile. Newer secrets-webhook/secret-init names and annotation
prefixes are a separate toolchain choice; do not silently mix examples across versions.

Provision an OpenBao namespace team-a, a KV v2 engine secret/, and a workload-cluster
auth mount. Bind demo-api-read to only ServiceAccount demo-api in namespace demo-app.
For Kubernetes auth, configure the correct remote API/CA and TokenReview identity
with reviewed permissions. Do not grant TokenReview to every application by default.
The example application config is a fragment: auth connection setup and configurer
credentials must already exist. Never use an unsealer role as an application role.

Application env contains vault:secret/data/demo/api#API_KEY. A fresh Pod resolves it
at startup; the secret value is not placed in the Deployment or an application Secret
in this injection mode. It still exists in process memory/environment and is available
to sufficiently privileged runtime users. Do not claim that it is inaccessible.

For Reloader 0.5.1, verified in source:

- Explicit spec.template.metadata.annotations opt-in is required.
- Deployment, StatefulSet and DaemonSet are supported; arbitrary resources/Jobs are not.
- Unversioned references in container/init-container env and supported from-path
  annotations are collected; a pinned #version intentionally opts out of latest-version reload.
- It polls KV data and reads metadata.version from that response. It receives the
  secret values too: a metadata-only ACL is insufficient for this implementation.
- It patches secret-reload-count in the Pod template. It does not rewrite live env,
  rotate the source credential, or guarantee zero downtime.
- A StatefulSet/DaemonSet with OnDelete does not automatically replace existing Pods
  merely because its template changed. Validate update strategy and application readiness.
- Kubernetes list/watch scope, namespace-scoped patch permissions, CA Secret read and
  exact OpenBao read paths must be reviewed independently.
- Flux/Helm drift handling must preserve or ignore only the controller-owned reload-count
  annotation, not all workload drift. Include this in integration tests.

Use a dedicated reloader role and ServiceAccount, not root. A single-server example
is supplied in [reloader-values.yaml](./examples/reloader-values.yaml); production RBAC and namespace scope must be added
by the integration. The upstream README labels v0.5.1 early alpha and not recommended
for production. This must be visible to users, not hidden by a successful smoke test.

Sources: [Reloader v0.5.1 README](https://github.com/bank-vaults/vault-secrets-reloader/blob/v0.5.1/README.md),
[collector](https://github.com/bank-vaults/vault-secrets-reloader/blob/v0.5.1/pkg/reloader/collector.go),
[Vault reader](https://github.com/bank-vaults/vault-secrets-reloader/blob/v0.5.1/pkg/reloader/vault.go).

#### Upstream OpenBao fix contributed by this reference deployment

We contributed [vault-secrets-reloader#702](https://github.com/bank-vaults/vault-secrets-reloader/pull/702),
merged on 2026-11-02 and included in [v0.5.1](https://github.com/bank-vaults/vault-secrets-reloader/releases/tag/v0.5.1),
published on 2026-11-03. It removes the implicit default namespace when VAULT_NAMESPACE
is unset and uses a separate namespace-free client for /sys/health. Application/auth
requests retain the configured namespace. Regression tests cover health request headers
and preservation of the original client's namespace, including concurrent token updates.
The PR reports real OpenBao 2.7.1 dev-server checks for root and child namespaces.
Our deployed reference has since moved to OpenBao 2.7.2 with Reloader 0.5.1.

Thus the namespace/authentication/health blockers are fixed in a published release;
a private image or local patch is not required for those fixes. This does not remove
the upstream early-alpha status or replace the KV-change-to-workload-rollout acceptance
test for the proposed Cozystack package. Other v0.5.1 fixes, such as read-error state
preservation and rollout retry, are separate contributions, not claimed as ours.

## User-facing changes

The existing OpenBAO kind and Helm default stay available. The new backend would be
an explicit choice for fresh instances, with image/controller versions managed by
the platform. Tenant inputs reference approved TLS and unseal resources rather than
arbitrary operator configuration. Administrators opt workload clusters/namespaces
into injection; applications use documented annotations and secret references.
Reload has a separate explicit opt-in and visible experimental status.

## Upgrade and rollback compatibility

For the first iteration, support fresh instances and client integration with existing
servers. Existing Helm-managed instances must not switch backend through a normal upgrade.
Their nested HelmRelease may be pruned, and the current chart explicitly deletes data
PVCs when its StatefulSet is deleted. A single-replica source also uses file storage,
so a Raft snapshot cannot be assumed available there.

A later migration procedure must establish a compatible data backup and separately
protected matching unseal/recovery material, rehearse restore, record PVC identity and
ownership, preserve storage before Helm deletion, then transfer workload ownership.
Names, service selectors, TLS SANs, auth addresses and backup jobs must all be reconciled.
For file → Raft, require an independently tested storage migration, not volume reuse.
Rollback after writes to the target needs a data plan, not just a Git revert.

Raft snapshots are created by OpenBao, scheduled/uploaded by a separate integration.
Use Cozystack bucket/credential abstractions, restricted policies, read-back integrity
checks, retention and stale-backup alerts. Test restore with matching key material in
an isolated environment. Velero integration documented by Bank-Vaults is not evidence
that every storage snapshot is an application-consistent Raft backup.

## Security

External-unseal files retain the earlier tested design: a short-lived projected JWT,
root auth on the external server, namespace-qualified KV paths and a read-only runtime
policy. This is automated Shamir, not OpenBao Transit seal. The primary application
namespace and the external key-store namespace are independent despite similar names.

A read-only runtime role cannot initialize an empty server and write its new shares.
Use a recoverable, explicit bootstrap workflow; define how interruption between init
and durable key storage is recovered. Preprovisioned shares must match restored data.
The backend must never reinitialize an existing PVC or generate replacement seal keys
when expected material is missing. preFlightChecks: false in the tested reference is
not a recommended default; review it before productizing.

Native self-init in community#134 is a real alternative. Compare it with Bank-Vaults
init before choosing credential handling; do not run both initializers. Similarly,
choose native Transit/KMS or Bank-Vaults-managed Shamir deliberately. storeRootToken:
false does not automatically remove existing tokens or solve configurer authentication.
A continuously privileged configurer needs a documented scope and credential lifecycle.

External OpenBao must have its own independent key protection and recovery path.
Bank-Vaults documents Kubernetes-Secret key storage as dev/test only; SOPS protects Git,
not runtime Secret/etcd access. Do not make it the production default for the external
trust anchor. Automated unseal grants its service quorum access; it is not a human quorum.

Audit devices should be rendered using OpenBao's declarative server configuration.
Do not assume Bank-Vaults' older API-based audit configuration works unchanged with
OpenBao. Similarly, monitor/test TLS renewal separately from application KV reload;
Vault Secrets Reloader is not a TLS-file watcher for the OpenBao server.

Sources: [unseal backends](https://bank-vaults.dev/docs/concepts/unseal-keys/),
[native Transit](https://openbao.org/docs/configuration/seal/transit/),
[declarative audit](https://openbao.org/docs/configuration/audit/).

## Failure and edge cases

- Missing or mismatched unseal material: remain sealed and report the error; never
  generate replacement keys or reinitialize existing storage automatically.
- External key-store outage: already unsealed servers may continue serving, but
  restart recovery depends on restored connectivity or independently held keys.
- Wrong CA, JWT audience, ServiceAccount or KV path: deny access, surface failure,
  and retain existing configuration/data.
- Selected application admission without a healthy webhook: fail closed; unselected
  workloads remain outside the webhook's selection scope.
- Missing application secret: block startup; do not log values or silently use an empty value.
- Reloader read/patch failure: preserve the last known state and retry; verify this
  with a synthetic integration test rather than inferring success from Pod readiness.
- Interrupted initialization: preserve evidence, storage and key material; require a
  tested recovery procedure before calling fresh bootstrap supported.

## Testing

Reference deployment: OpenBao 2.7.2, Bank-Vaults 1.33.2, Vault Operator 1.24.1;
webhook 1.23.1, vault-env 1.23.0 and Reloader 0.5.1.
Prior operational checks include upgrade/restart/unseal, scoped JWT access, namespace
migration, application authentication/read, off-site snapshots and isolated restore.
These do not certify upstream OpenBao compatibility, HA or shared-tenant isolation.
The newest post-cleanup snapshots were not separately restore-tested.
The proposed API and newly sanitized examples have not been deployed as a new stack.

Required acceptance tests:

1. Existing default app renders unchanged; new fields cannot bypass tenant boundaries.
2. Fresh install and interrupted bootstrap recover without losing keys or reinitializing data.
3. Restart/unseal and wrong CA/audience/subject/role/path; unavailable external key store.
4. TLS leaf/CA renewal, actual served certificate and auth after controlled restart/reload.
5. Git change to auth/policy reaches OpenBao; reconcile failures are visible and retryable.
6. Injection works without storing application values in Kubernetes; denied paths fail closed.
7. A synthetic KV update rolls only opted-in consumers; pinned versions remain unchanged.
   Test denied patch, temporary API failure, restart of Reloader and Flux reconciliation.
8. One-replica limits and rollout downtime documented; HA quorum tests before advertising HA.
9. Snapshot read-back and isolated restore with the original keys, including namespaces/auth.
10. Delete/upgrade/migration explicitly verify storage and key retention; no accidental prune.

## Rollout

1. Agree on the design, ownership and security boundaries.
2. Package scoped workload injection for existing managed/external OpenBao.
3. Add separately enabled experimental Reloader with KV-change-to-rollout tests.
4. Add the operator backend and configuration integration for new instances.
5. Handle existing-instance migration in a separate, restore-tested change.

Maintain a tested image/chart matrix and review the older webhook toolchain before
selecting versions for Cozystack. No existing backend is deprecated by this proposal.

## Open questions

- Which requirements justify an operator backend over the native self-init/Transit path?
- How should tenant references to TLS, unseal and configuration resources be authorized?
- Which identity and lifecycle should a continuously running configurer use?
- Should experimental reload ship as a platform package or a documented external add-on?
- Which TLS renewal mechanism and independent external-key protection profile should
  be part of the first supported configuration?

## Alternatives considered

**Native Helm + self-init/Transit.** Preserves existing ownership and can avoid exporting
an initialization root token. Prefer it when it meets the requirements; it does not
prevent independent Bank-Vaults injection. The optional operator backend is proposed
for its lifecycle and configuration integration, not because native OpenBao cannot unseal.

**ESO only.** Appropriate when applications consume Kubernetes Secrets. Keep that
path; direct injection is an additional choice for applications that do not need
persisted Kubernetes Secret values.

**Agent/file injection or CSI.** Useful for applications that consume files or support
live reload. Their lease, filesystem and application-reload behavior needs a separate
supported profile; the initial reference focuses on environment injection.

**Manual workload restart.** A valid alternative to experimental Reloader. A controlled
rollout picks up fresh injected values without adding a secret-reading controller.

**Mandatory platform-wide replacement.** Adds dependencies for every installation and
risks changing existing server ownership. An optional, staged integration is preferable.
