# Managed credential lifecycle

- **Title:** `Managed credential lifecycle: minted passwords, verifier-only storage, attributable history`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-09-28`
- **Status:** Draft

## Overview

A generated managed-service password is readable today as many times as anyone likes, through four independent routes, and hiding the reveal button changes none of them. This proposal replaces generation-at-render with a mint operation on the aggregated API: the platform generates the password from a CSPRNG, stores only what the engine needs to verify it, and returns the plaintext once, in the response body. Where an engine accepts a verifier the platform keeps no password at all. Where it cannot, the row says so and names the component that holds the plaintext.

Phase 1 covers PostgreSQL, MariaDB and ClickHouse users, whose engines accept a verifier and for which a delivery path exists, plus the disclosure half of the OpenSearch admin account. OpenSearch tenant users are not in phase 1: a declared user does not reach the engine at all today, and repairing that comes before converting it. Classes that need plaintext by engine necessity are classified here and converted later. Tenant-supplied secrets are not in scope; they belong to [#82](https://github.com/cozystack/community/pull/82). The mechanism-level specification is in [details.md](./details.md); this document is the contract to review.

## Scope and related proposals

**Supersedes [cozystack/community#72](https://github.com/cozystack/community/pull/72).** Ownership of `<release>-credentials` and rotation mechanics cannot be decided in two proposals, so this one takes credential ownership, rotation execution, bootstrap and ownership migration. It builds on @scooby87's analysis and on the failure cases in [the original discussion](https://github.com/cozystack/community/issues/71).

- [Tenant-supplied secrets](https://github.com/cozystack/community/pull/82) owns credentials the tenant brings, the store behind them and the reference syntax from [#37](https://github.com/cozystack/community/pull/37). The boundary between the two proposals is the source of the bytes. Whether a deployment permits a supplied value where the platform could generate one is policy, and this proposal decides it.
- [PostgreSQL/MariaDB password cleanup](https://github.com/cozystack/cozystack/pull/4078) merged on 2026-09-25 and removed the inline password field for those two engines only. ClickHouse and OpenSearch still accept `users.<name>.password` in their values, so route one stays open for them and closing it needs its own change, named per adapter rather than assumed.
- [API v1](https://github.com/cozystack/community/pull/73) must carry the removed-field changes in its schema transition, so an older client cannot silently drop a field and trigger generation.
- [Unified TLS/PKI](../unified-tls-pki/README.md) keeps certificate issuance and trust management. This proposal classifies public trust and private key material and restricts disclosure.
- [cozystack/cozystack#4164](https://github.com/cozystack/cozystack/issues/4164) is closed by the service-account split below for converted classes only. A class whose credentials Secret still holds plaintext keeps the ancestor grant the issue is about, so the issue closes adapter by adapter rather than on this proposal landing.

## Decisions

<!-- Left empty in the initial PR, per ../README.md#decision-records. Records live under ./decisions/, numbered from 0001, and are linked here newest first once an implementation choice is settled or changes. -->

## Context

Baseline: [cozystack at 8799a4e4a](https://github.com/cozystack/cozystack/commit/8799a4e4a), before [#4078](https://github.com/cozystack/cozystack/pull/4078) merged. Proposals above that are still open are dependencies, not shipped behavior.

- Applications are virtual views of HelmReleases; their spec becomes Helm values and is returned unredacted, so an inline password is readable by anyone who can read the application. An Application reports the backing HelmRelease's UID as its own, so no identifier distinguishes one incarnation of an account from another.
- The `tenantsecrets` projection returns whole Secret `data`. A write through its registry stamps the outward-projection marker.
- Every application chart grants `get/list/watch` on its credentials Secret by name to `use`-tier groups, the tenant ServiceAccount, and the ServiceAccounts of every ancestor tenant, through the `<release>-dashboard-resources` Role. Removing the projection alone leaves this route open, and the reverse.
- Chart-rendered Secrets stay in Helm release history for `MaxHistory` revisions, so a chart-side regeneration cannot retire the old bytes. `lookup` plus random generation is render-time behavior, not an observed dependency.
- The dashboard list transfers Secret data before the reveal button is pressed.
- The same Secret holds tenant accounts and service accounts. MariaDB `root` sits next to every tenant user; the ClickHouse `backup` account is read from it by the backup CronJob and the backup sidecar; Harbor's `redis-password` is read from it by Flux through `valuesFrom` on every reconcile.
- Cozystack collects the kube-apiserver audit log: fluent-bit tails `/var/log/audit/kube/*.log` into VictoriaLogs, and `monitoring-agents` is a default package. Talos ships a default policy of one catch-all `Metadata` rule. The aggregated API server runs with no audit flags of its own, so every record of a call to it comes from kube-apiserver. Other distributions ship no policy by default.

### The problem

A viewer learns an inline password from application discovery. A user denied a second dashboard reveal fetches the same Secret through the raw grant. An audit record saying that a backend read a Secret cannot say which person asked. And the platform's own service accounts are exposed by the same grants as the tenant's, because they share one object.

Rotation also has to handle disagreement between engine state and published state: a database can accept password B while the platform still advertises A if the writer fails before the change is observed. This is a failure the design must handle, not a claim that the sequence has been reproduced.

These are separate failures. A successful rotation invalidates the old password even while historical bytes remain recoverable; erasing history and terminating sessions are further operations, not the same one.

## Goals

- Discover an authorized account, its endpoint, database, public trust, capabilities and lifecycle state without transferring protected material.
- Mint and re-mint a password per account in one authorized call, preserve grants, and store only the engine's verifier wherever the engine accepts one.
- Separate the three states a caller may mean by revocation: password replaced, login denied, account removed.
- Keep a per-account record carrying account identity, issuance time, initiating identity, previously-exposed marking and the engine's applied state, readable by the tenant.
- Split the platform's own service accounts out of tenant-facing Secrets, and state per class which component holds plaintext and why.
- Report supported operations per service through API capabilities, backed by adapter acceptance tests; reject unsupported operations before mutating an engine.

### Non-goals

The first release does not provide a secret store, tenant-supplied inputs, arbitrary secret sharing, scheduled rotation, dynamic database leases, delivery to VM guests or to other tenants' clusters, automatic restarts of arbitrary workloads, universal zero-downtime replacement, or automatic session termination. Grants and ACLs stay engine-specific, but a re-mint must preserve them.

It does not erase recipients' copies, clean historical backups, protect runtime plaintext from host and control-plane administrators, or rotate every system identity. None of that permits protected bytes in new logs, broadly readable specs, or tenant-facing projections. Unsupported classes stay visible in the coverage table and are excluded from any compliance claim.

Encryption at rest for etcd resources and snapshots, storage encryption, and encryption-key management are outside this proposal. Calling an internal Secret protected means access and disclosure are restricted; it does not mean this API encrypts stored bytes.

## Design

### Proposed choices

These are the choices proposed for review, not claims of approval or of completed implementation. API names, wire shapes, HTTP mappings and numeric limits are provisional defaults, collected in [details.md](./details.md#operating-limits); changing them must preserve the failure semantics defined here.

| Question | Proposed answer | Why |
|---|---|---|
| What is stored | The engine's verifier, wherever the engine accepts one. The plaintext exists in the mint response and nowhere else. | Nothing to collect twice, so one-time disclosure stops being an entitlement the server has to guard. [Storage](./details.md#identity-storage-and-writers). |
| Who generates | The aggregated API server, for every class. | Render-time `lookup` puts bytes in release history and gives a different answer per chart. Sprig can compute some verifiers and not others, which is why this is not left per chart. |
| Lost response | A lost response after a successful mint is answered by minting again. No replay, no acknowledgment window. | The API can allow at most one response carrying the plaintext; it cannot prove a person received it. |
| Concurrency | One mint per account at a time. The record admits the caller with an expected-version precondition, and the write of the material is refused if the record has moved on since. | A mint is a destructive replace, so a stale caller must not be able to overwrite the winner's material after losing the race. |
| Applied state | The API reports issuance. Whether the engine applied it is the adapter's observed condition, reported on the record. | Verification before publication is given up; see [the trade](#what-is-given-up). |
| Read paths | Remove tenant-facing material from application representations, the legacy projection, raw dashboard grants and future chart renders. Split service accounts into their own Secret. | Every effective route must obey the policy. [Security](#security). |
| Service accounts | Plaintext where a machine consumer needs it, in a Secret no tenant role is granted and no `ApplicationDefinition` selects. Charts keep rendering it. | The platform is its own client; a verifier cannot replace a value a component must present. |
| Rights | Separate discovery, mint, revoke and history. Declaring an application's accounts carries no authority to mint them. | Minting returns the plaintext, so the authority to mint is the authority to read. [Authorization](#authorization). |
| Audit | kube-apiserver Metadata-level audit for the call, plus a per-account record for what that log cannot carry. | The audit event names the principal on the request and the object, and never the body; it cannot say a password was applied or be read by a tenant. |
| Coverage | Phase 1: the classes that are verifier-only end to end, engine and operator both. Everything else is classified in the table and converted later, per row. | An engine that accepts a verifier is not enough; something has to deliver it without a full chart render. |
| Compatibility | Strict is the default for new installations per converted adapter. An unconverted service stays installable and reports `Legacy`. A deployment that sets `strictOnly` refuses to install one instead. | Previously exposed bytes cannot become undisclosed by changing a label. [Compatibility](#upgrade-and-rollback-compatibility). |

### Model

Two public surfaces in `core.cozystack.io`: a `Credential`, which is metadata and lifecycle state of one engine account, and a mint call on it. No credential material is served by any read.

```yaml
apiVersion: core.cozystack.io/v1alpha1
kind: Credential
metadata:
  name: cr-7c39
  namespace: tenant-example
spec:
  application: {kind: Postgres, name: orders}
  account: app
  credentialClass: password
status:
  phase: Usable
  accountRef: {incarnation: 3}
  issuance: {at: "2026-09-28T10:14:02Z", by: "alice@example.org", version: cv-91ae}
  applied: {version: cv-91ae, observedAt: "2026-09-28T10:14:19Z"}
  previouslyExposed: false
  connection: {host: orders-rw.tenant-example.svc, port: 5432, username: app, database: orders, trustRef: orders.tenant-ca}
  capabilities: {mint: true, denyLogin: true, removeAccount: true, terminateSessions: false}
```

| Interface | Semantics |
|---|---|
| `GET/LIST/WATCH credentials` | Filtered metadata, lifecycle state and public connection data. No material, ever. |
| `POST credentials/{name}/mint` | Generates a password, computes the engine's verifier, writes it where the adapter specifies, records issuance, and returns the plaintext in the response body. |
| `POST credentials/{name}/revoke` | Takes the intended state: `DenyLogin` or `RemoveAccount`. Replacing the password without disclosure is `mint` with the response discarded, which the API does not offer as a separate verb. |
| `POST credentials/{name}/allowLogin` | Reverses `DenyLogin` without minting. A denied account that is minted stays denied, because a new password is not a decision to let the account back in. |

The mint call is the only path that produces material. It requires an expected-version precondition, so a second caller racing the first is rejected rather than silently superseding it. Editing unrelated options, dry-run, rendering and GitOps reapply never mint. There is no counter in application values that triggers rotation, and declaring `users:` in an application creates the account without a usable password until someone mints it.

Behind the API, one record per account persists in a platform namespace, bound to the tenant namespace UID, the HelmRelease UID and a server-assigned incarnation. It carries identity, issuance time and initiating identity, the previously-exposed marking, and the adapter's observed applied state. It never carries material or a fingerprint of material. Wire details and error mapping are in [details.md](./details.md#public-api-and-request-semantics).

### What is given up

The old contract promised that a new password is published only after the engine accepted it and the old one was proven rejected. A synchronous mint cannot promise that: the response returns before any engine has seen the value. The API reports issuance; the adapter reports applied state on the record afterwards, and a caller that needs proof waits for it.

The cost is real. A caller can hold a password that does not work yet, or never works because the engine never converged. The record makes that state visible rather than hiding it, and re-minting is the recovery. This is accepted deliberately, in exchange for never holding plaintext the platform could leak.

### Service accounts

The platform is a client of the services it runs. The ClickHouse backup account, MariaDB root and Harbor's redis password are read by machine consumers on every reconcile or every backup run, so a verifier cannot replace them. The OpenSearch operator already reads its admin credential from a separate Secret; what sits in the tenant-facing one is a second copy of the same account, including a connection URI with the password in it, and that copy is what goes away.

Those accounts move out of `<release>-credentials` into a service Secret per release that no tenant role is granted, that no `ApplicationDefinition` selector matches and that the dashboard resource map does not name. Charts keep rendering that Secret with `lookup` plus random generation, which is unchanged behavior and keeps bootstrap ordering as it is today. The consequence is stated rather than hidden: for this class the plaintext stays in Helm release history, so that route remains open for service accounts and closed for tenant accounts. Their rotation stays with the engine maintainers and is out of scope here.

### Revocation

Three states a caller may mean, kept apart because engines keep them apart:

- **Password replaced.** The old password stops working; the account remains and remains reachable. This is a mint whose response the caller discards.
- **Login denied.** The account exists and cannot be used to authenticate: `login: false` on PostgreSQL, `ACCOUNT LOCK` on MariaDB, ACL `off` on Redis and Valkey. A denied account stays denied through a later mint; only `allowLogin` reverses it.
- **Account removed.** The account is gone, and its declaration goes with it, because an account an application still declares is recreated on the next reconcile.

Where the deny lives decides whether it survives. An operator that reconciles an account from a declaration reasserts that declaration, so a deny written into the engine is undone on the operator's next pass, and a deny patched onto the live custom resource is undone by the next chart render. The deny has to reach the declaration itself, which for these engines means the application's own values: the API writes a per-account flag there and reasserts it from the record on every write it makes. The cost is that denying and re-allowing are `helm upgrade`s, so neither is available on a release that is suspended or already failed.

None of the three terminates sessions already open. Where an engine offers a separate operation for that, the adapter may expose it as a capability; where it does not, `terminateSessions` reports false and the proposal makes no claim. Per-engine detail is in [details.md](./details.md#revocation-per-engine).

### Authorization

Kubernetes RBAC is additive, so the aggregated API checks both the verb and a server-controlled account grant under the effective policy. Policy is a ceiling: platform restrictions apply first, ancestors may tighten, children cannot loosen.

An account grant names one account of one application, the operations it permits, and an expiry. A local administrator of the tenant that owns the application issues it; nobody can issue a grant wider than the authority they hold, and an ancestor's policy caps every grant below it. Revoking the grant is deleting it, and it does not reach back into a password already minted under it. The grant's storage and wire shape are in [details.md](./details.md#authorization-detail).

| Effective authority | Metadata and connection data | Mint | Revoke | History |
|---|---|---|---|---|
| `view` | Authorized apps | No | No | Sanitized outcomes |
| `use` | Authorized apps | No by default | No by default | Sanitized outcomes |
| Local `admin` / `super-admin` | Local apps | Tenant-facing accounts | Tenant-facing accounts | Detailed local events |
| Tenant automation identity | Local apps | No | No | Detailed local events |
| Explicit account grant | Named account | If granted | If granted | Granted scope |
| Workload identity | No | No | No | No |

Minting returns the plaintext, so the authority to mint is the authority to read the password. A grant that previously allowed regeneration without disclosure has no equivalent here, which costs the tenant automation identity its ability to provision a working application unattended: it creates the accounts, and a human or an authorized external API consumer mints them afterwards. Whether that cost is acceptable, or whether a replace-without-disclosure operation should exist for automation, is an [open question](#open-questions). Internal service accounts are not tenant-facing at any tier.

### Security

The disclosure routes and what closes each, per class:

| Route | Tenant accounts | Service accounts |
|---|---|---|
| Inline field in the application spec | Removed for PostgreSQL and MariaDB by [#4078](https://github.com/cozystack/cozystack/pull/4078); still present for ClickHouse and OpenSearch, and closed for them by their own conversion | Not applicable; never in the spec |
| `tenantsecrets` projection | Verifier only, nothing to disclose | Not selected by any `ApplicationDefinition` |
| Per-release raw grant to `use` tier and ancestor ServiceAccounts | Verifier only, nothing to disclose | Not granted, not in the dashboard resource map |
| Helm release history | Chart stops rendering tenant plaintext. For ClickHouse the verifier does pass through release values, which is a verifier and not a password | **Open by design.** Plaintext stays in history for this class |
| Operator-materialised configuration | ClickHouse writes the verifier into a ConfigMap in the tenant namespace; no tenant role grants `configmaps` | Not applicable |

A verifier is not treated as protected material. It is not returned by discovery, not projected outward and not written into logs, because no consumer other than the engine needs it; its presence in a Secret or in release history is not a breach of the one-time contract. One caveat is carried explicitly: ClickHouse stores an unsalted single-round SHA-256, so a password a tenant chose can be recovered from it by dictionary attack. A password minted from the CSPRNG at the length this proposal requires cannot.

The aggregated API server is a trusted component with fleet-wide Secret access. It already holds that access today. Per-namespace RoleBindings on one identity do not partition its compromise, and this design says so rather than claiming isolation.

Audit evidence is append-only for tenant and API actors. This design does not promise tamper resistance against a compromised control-plane administrator.

### Coverage

The table describes proposed phases, not existing capabilities. An absent adapter reports its capabilities as false and rejects the operation before touching an engine. A phase is complete only when all rows agreed for that phase pass.

| Service / account | Verifier | Phase and writer | Replacement evidence | Remaining dependency |
|---|---|---|---|---|
| PostgreSQL users | Yes, SCRAM-SHA-256 | 1; API mints, CNPG `managed.roles` applies | Fresh login with the new password, old login rejected, roles and grants unchanged | Chart moves password and role attributes to `managed.roles` and gains a per-account `denyLogin` flag; init path keeps existence, objects and orphan cleanup, and needs fixing to reassign owned objects outside the `postgres` database |
| MariaDB users | Yes, `passwordHashSecretKeyRef` | 1; API mints, mariadb-operator applies | Fresh new and old login tests, grants unchanged | Requires the watch label on the Secret; operator behavior on missing input and user recreation must pass |
| ClickHouse users | Yes, `password_sha256_hex` | 1; API mints into a values Secret, Helm renders it into the CHI | New and old authentication on every replica, grants unchanged | Chart takes the verifier from `valuesFrom` instead of computing it, and `users.<name>.password` leaves the values in its own change. Every mint is a Helm revision; the unsalted hash is a stated weakness for tenant-chosen passwords; the backup account moves to the service Secret |
| OpenSearch tenant users | Engine yes through `internal_users.yml`; the operator's user surface takes no hash | Later, and blocked on a repair | New and old authentication, roles unchanged | A declared user never reaches the engine: the chart renders a labelled Secret nothing consumes and creates no `OpensearchUser`. That is a defect to fix before the class can be converted, and fixing it decides whether the writer is the operator, which means plaintext, or securityconfig, which means a verifier |
| OpenSearch admin | Not applicable | 1, for the disclosure half only | The tenant no longer receives the credential, and the credential is rotated | The tenant-facing Secret holds a copy of the operator's admin account with a URI containing the password. Removing it does not un-disclose it, so the migration rotates it |
| NATS, RabbitMQ, Redis, Valkey | Engine yes, our operator path no | Later, per adapter | Per-engine authentication tests | Each conversion decides whether to change the operator path or accept plaintext and say so |
| MongoDB | No; the server computes SCRAM from plaintext | Later | New and old authentication, roles preserved | Plaintext by engine necessity. `dropUser` needs an executor |
| Bucket keys, COSI | No; SigV4 signs with the secret itself | Later | Authorized storage action with the new pair, prior issuance rejected | Plaintext by engine necessity; disclosure is a policy on the interface, not a property of storage |
| Harbor, Qdrant, Outline | No | Later | Per-engine | Plaintext by engine necessity. Harbor also needs an API caller for any post-bootstrap revocation |
| Kafka | Engine yes, Strimzi `KafkaUser` no | Later | Protocol authentication plus topic ACL tests | No user surface exists in the chart today; that feature comes first |
| Managed Kubernetes: OIDC kubeconfig | Not applicable | 1, classification only | Repeatable public discovery; cluster-admin follows the identity claim | Already conforming wherever OIDC is enabled |
| Managed Kubernetes: static `super-admin` kubeconfig | Not applicable; a private key | Phase open | Fresh admin access with the replacement and verified loss of the old | Certificate reissue is not revocation. Retirement is either client-CA rotation or issuance under a `KubeconfigGenerator` identity that can be de-authorized |
| Virtual machine `cloudInit` | Not applicable | Out of scope, named | None | Free-form user data in the application spec, readable by anyone who can get the application. No schema check or reference field can catch a password inside it |
| TLS private keys, CA keys, Talos and bootstrap secrets | Not applicable | Internal; issuance outside this API | No tenant mint or revoke | PKI and node-lifecycle maintainers keep their responsibilities |

## Upgrade and rollback compatibility

A service is `Strict` when every credential-bearing field it has goes through this API, and `Legacy` while any of them still carries an inline value. Strict is the default for new installations per converted adapter; an existing installation gets a finite migration window per adapter, then an explicit mint before the service can report `Strict`. A deadline cannot precede the adapter it depends on. `strictOnly` is a platform-level setting that refuses to install a service reporting `Legacy` at all, rather than installing it with the label.

Until the reference capability from [#82](https://github.com/cozystack/community/pull/82) exists, a field that can only carry a tenant-supplied value keeps its inline form and the service reports `Legacy`. Whether that exception is scoped to the field or to the service is an [open question](#open-questions).

Migration runs in three stages, as a one-shot platform migration rather than a resident controller.

Stage A is preparation and changes no authentication. It annotates the tenant credentials Secret with `helm.sh/resource-policy: keep` so a later chart change cannot delete it, and it creates the service Secret with the service accounts copied into it. Both Secrets exist afterwards and nothing is removed, so a rollback at this point loses nothing.

Stage B computes verifiers from the plaintext the platform already holds and writes them where each adapter specifies. Where the adapter's target is a new object, as it is for PostgreSQL, the object is created and nothing existing is touched. Where the adapter's target is the existing Secret, the verifier goes under a new key rather than over the plaintext one, because the old chart and the old operator still read the old key and would take a hash for a password.

Stage C flips the readers and the writers in one release: the charts stop rendering tenant material and start pointing at the verifier, and the migration takes field ownership with the forced server-side apply the existing migrations use. Helm does not release `managedFields` ownership when a template stops rendering a field, so the transfer is performed rather than awaited. Every account converted in this stage is marked previously exposed until re-minted.

The ordering is what keeps a chart and the API from writing one object at the same time, and it is also what makes a rollback between stages harmless.

Rollback of a converted application to a pre-conversion chart revision does restore the old plaintext: the rendered Secret is in release history, `helm rollback` applies it, and MariaDB's operator will re-apply the password it finds. The old exposed password becomes live again. The previously-exposed marking survives, so the record still says the account is exposed, and the recovery is a mint.

## Testing

Adapter acceptance per engine: a minted password authenticates, the old one is rejected, grants are unchanged, and the applied state appears on the record. Revocation per state: login denied is denied, account removal is removed, and each survives the operator's next reconcile. Isolation: guessed names, forged labels, manipulated selectors, sibling and ancestor references, raw and projected reads. Migration: each stage on a populated cluster, the ownership transfer, and a rollback between stages. The detailed plan is in [details.md](./details.md#acceptance).

Attribution rests on the kube-apiserver audit log, so an installation whose distribution ships no audit policy cannot claim `Strict`. Talos ships one and Cozystack collects it; anything else is the installation's responsibility and is checked before strict mode is offered.

No live engine, migration or browser test is claimed as executed by this proposal.

## Open questions

- **The write target differs per engine.** PostgreSQL takes a per-user basic-auth Secret because that is what CNPG watches; ClickHouse takes a values fragment because that is what reaches its operator. Neither is the single credentials Secret the review discussion assumed, so the mint API writes wherever the adapter says rather than to one well-known object, and the `ApplicationDefinition` selectors, the dashboard Role and the migration follow per adapter. Whether that per-engine spread is acceptable, or worth constraining, is a design call this proposal makes and the reviewers should confirm.
- **Who owns OpenSearch users after the repair.** A declared tenant user reaching no engine is a defect, and this proposal records it rather than fixing it. Whoever does fix it chooses between the operator's `OpensearchUser`, which takes plaintext and makes the class plaintext-by-operator, and securityconfig, which takes a bcrypt hash and makes it verifier-only at the cost of bypassing the operator's user API. The two overwrite each other, so it is one or the other, and the choice decides which row OpenSearch eventually occupies.
- **What a mint means on a release that cannot upgrade.** ClickHouse applies a mint through a Helm upgrade, and PostgreSQL's deny-login does the same. A suspended or failed release takes neither, so the operation is accepted, recorded and reported as pending. Whether pending is the right answer, or the call should be refused outright, is a product question rather than a mechanical one.
- **`Legacy` granularity.** Per service today. A converted PostgreSQL whose backup credential still has no reference path would be `Legacy` as a whole, which understates what works. Per-field is new semantics and needs its own rules.
- **Replace without disclosure.** Minting is disclosure, so the automation identity that provisions applications cannot produce a working one unattended. Whether a replace-without-return operation should exist for that identity, and what stops it from being used to hand a password to someone who may not read it, is unresolved.
- **What retaining records costs.** Keeping the exposure history across a delete and recreate means records outlive the applications that created them, keyed by the account rather than by the release. They are closed rather than removed and expire a year later. That is unbounded growth in a namespace nobody looks at, traded for a recreated account not presenting itself as never exposed. The retention number is a guess and wants a real one.
- **The API writing into application values.** Deny-login needs a per-account flag in the values, and application values have no field ownership: an update replaces them whole, and the tenant, the platform and the restore driver already write them. The API reasserting its flag from the record on every write is the mechanism the platform uses for its shard label, and it is a convention rather than an enforcement. Whether that is enough, or the platform needs real field ownership on application values, is bigger than this proposal.
- **Initiating identity behind a backend.** The record names the principal on the request. When the dashboard or an external API consumer calls on a person's behalf, that principal is the backend unless a delegation transport carries the person's identity. That transport is not specified here.
- **Session termination.** Left as a per-adapter capability. Whether any class should require it before claiming strict is unresolved.

## Alternatives considered

**Keep generation in Helm or in a template helper.** Rendered material enters release history and `lookup` is not a dependency. Accepted deliberately for service accounts only, where the consumer is a machine and the Secret is not tenant-facing.

**Keep one-time collection with an entitlement and a window.** It exists to guard plaintext the platform holds. With verifier-only storage there is nothing to collect a second time, so the entitlement, the window, the consumption state and the commitment protocol have no subject.

**A durable journal with a transactional outbox and an exporter.** The apiserver's Metadata-level audit event names the initiator and the object and never the body, and Cozystack already collects it. What it cannot do is address a credential, be read by a tenant, or say the engine applied anything, and the per-account record covers those three.

**Hide the dashboard button and keep raw reads.** The same principal uses another client; grants and a stated boundary are enforceable, client type is not.

**Return values only from create, or allow replay until acknowledgment.** This is the shape adopted here, and the old objection to it had two halves. Asynchronous issuers do not fit a synchronous response, which is why COSI is not in phase 1. Unattended creation does not fit either, and that half stands: declaring an account no longer produces a working one, so a GitOps apply leaves accounts waiting for a mint. The cost is recorded rather than argued away, and whether to buy it back with a replace-without-return operation is an [open question](#open-questions). Replay is still refused, because it releases the same bytes twice.

**Five kinds with operations, bindings and events.** Operations, bindings and an event kind exist to manage state that verifier-only storage removes. Delivery of credentials into a tenant's own cluster is a separate concern once a store exists, and is not replaced here.

**Require Vault or KMS.** External custody does not remove delivering bytes to engines that consume plaintext, and for engines that consume a verifier the platform already holds nothing. The classes that need a store are named in the coverage table and go to [#82](https://github.com/cozystack/community/pull/82).

---

<!-- Inspired by KubeVirt enhancement proposals and Kubernetes Enhancement Proposals (KEPs). -->
