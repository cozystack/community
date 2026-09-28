# Managed credential lifecycle: mechanism

Companion to [README.md](./README.md), which carries the contract. This document carries the mechanism: storage layout, request semantics, per-engine adapters, migration and acceptance. Where the two disagree, the README wins and this document is wrong.

## Identity, storage and writers

One record per engine account persists as a custom resource in a platform namespace, through kube-apiserver. It is keyed by the tenant namespace UID, the application's name and kind, the account name and a server-assigned incarnation. The HelmRelease UID is recorded but is not part of the key, because a deleted and recreated application gets a new one and the exposure history of an account has to outlive that.

Removing an engine username and later re-adding it inside a living application produces a new incarnation, and the previous incarnation's record is retained, marked closed, so its previously-exposed marking stays attached to the account that was exposed. Deleting the application retains its records the same way. What that costs is unbounded growth in a namespace nobody prunes; what it buys is that a recreated `orders` with a recreated `app` user does not present itself as never exposed. The retention bound is an operating limit rather than an open choice.

The record holds identity, issuance time, initiating identity, previously-exposed marking and the adapter's observed applied state. It never holds material, and never a fingerprint of material. It survives API-server restarts. A record whose engine-side material has disappeared reports `MaterialMissing`, which is a prompt to mint again, never evidence of a fresh install.

Records are created, closed and retained by the aggregated API server itself: a record appears when an application first declares an account, closes when the declaration goes away or the application is deleted, and is never edited by anything else. No resident controller participates. The one exception is the backup controller, which already patches an application on restore and therefore creates the records and the per-adapter material for a restored application as part of that path.

Each kind of material has exactly one writer:

| Material | Writer | Owner | Lifecycle |
|---|---|---|---|
| Tenant account verifier | The aggregated API server, on mint | This proposal | Replaced on every mint; no history kept |
| Service account plaintext | The application chart, at render time | Engine maintainer | Unchanged from today; rotation out of scope here |
| COSI keys and other native issuer output | The native issuer | Storage maintainer | Unchanged |
| Public CA and endpoints | Existing projection controllers | PKI maintainer | Unchanged |

The aggregated API server holds cluster-wide write on Secrets today, so minting adds no trust boundary that does not already exist. Namespace-scoping a shared identity does not reduce that identity's total authority, and this design states that rather than claiming isolation.

## Public API and request semantics

`GET/LIST/WATCH credentials` returns metadata, lifecycle state and public connection data. No field of the response carries material, a hash, an encoded value or a URI with a password embedded.

`POST credentials/{name}/mint` takes the credential UID and an expected version. It generates at least 32 characters from a CSPRNG, computes the engine's verifier, writes it where the adapter specifies, and returns the plaintext in the response body.

Three writes are involved and none of them can be one transaction with another, so the order and the guard both matter. First the record moves to a new version, with the issuance time, the initiating identity and an `Applying` state, under an expected-version precondition. Then the material is written, and that write carries its own guard: it is refused unless the record still holds the version this call claimed. Then the record moves to `Issued`.

The guard on the second write is what a version check on the record alone does not give. Without it, a caller that wins the first write and then stalls can wake up after a second caller has completed a whole mint, and overwrite the live material with its own older value while every precondition it checked was satisfied at the time it checked it. With it, the stalled caller finds the record moved on and writes nothing.

A crash between the writes leaves a record in `Applying` with no material change, or with material the record has not yet confirmed. Both are visible, neither is a silent divergence, and both are recovered by minting again.

There is no idempotency by client request ID. A replay cannot return the plaintext a second time, so a repeated call is a new mint with a new value.

`POST credentials/{name}/revoke` takes the intended end state, `DenyLogin` or `RemoveAccount`, and returns metadata. `POST credentials/{name}/allowLogin` reverses `DenyLogin`. A mint against a denied account replaces the password and leaves the deny in place: a new password is not a decision about whether the account may be used.

Replacing a password without disclosing it is a mint whose response the caller discards. The API does not offer that as its own verb, because a verb that hands the caller material it is supposed to throw away is a verb that invites keeping it.

Errors map as follows: a precondition failure on the expected version is `409` naming the current version; a class whose adapter does not implement the requested operation is `501` with the capability named; an unauthorized mint is `403` and changes nothing. When the adapter cannot complete the write, the call is `503` and the record is left in `Applying` rather than advanced, so the next caller sees the interrupted attempt instead of a version that moved for nothing.

## Operating limits

Provisional defaults. Changing them must preserve the failure semantics in the README.

- Minted password: at least 32 characters from a CSPRNG. Accounts migrated from chart-generated passwords carry whatever length the chart used, which is 16 alphanumerics; the previously-exposed marking covers their disclosure, not their strength, and only a mint replaces them.
- Mint request: 30 seconds, after which the caller must assume the write may or may not have landed and consult the record.
- Adapter applied state: observed within the engine adapter's own reconcile interval, and of different strength per engine. PostgreSQL reports the Secret version its operator applied, which is direct. ClickHouse reports that the configuration was written, which proves the ConfigMap and not the replica. MariaDB reports nothing per account at all. Only the first is observation; the other two are inference and are labelled that way on the record.
- Record retention: a closed record is retained for one year after the application that held it is gone, then removed. A record whose account still exists is retained indefinitely.
- Disclosure channel: the mint response is served with `Cache-Control: no-store`, never redirected, never retried by a proxy, and its body never enters a request log or a trace. A client renders it from the response it already holds and does not re-request to copy or download it.

## Authorization detail

The aggregated API checks the RBAC verb and a server-controlled account grant under the effective policy. An ancestor may tighten; a child cannot loosen.

An account grant is a namespaced object in the tenant's own namespace, served by the aggregated API and readable by the subject it names. It carries the application, the account, the subject, the permitted operations and an expiry that cannot exceed the policy ceiling of the tenant it lives in. A local administrator of that tenant creates it, and cannot create one carrying an operation they do not themselves hold. Deleting it ends the authority and does nothing to a password already minted under it. Grants are not inherited downward and an ancestor's administrator does not gain mint authority in a child by holding one in the parent.

Minting returns material, so mint authority is read authority. There is no grant shaped like "may replace this password but may not learn it": the operation that replaces it is the operation that returns it. A tenant automation identity that provisions applications therefore holds no mint authority, and provisioning a new application produces accounts without usable passwords until an authorized human or an authorized external API consumer mints them. That is a deliberate loss of unattended provisioning and it is recorded as an open question rather than as a settled improvement.

No tenant tier includes internal database root, maintenance superuser, system backup, CA private key or platform identity material. Tenant administrative access to a managed Kubernetes cluster the tenant owns is a separate tenant-facing class with its own adapter contract. A support grant must be time-limited, explicitly delegated in the child scope and audited. A fully privileged platform administrator remains outside the tenant confidentiality boundary.

Before strict activation, admission must restrict references to the service Secret, privileged ServiceAccounts and their pod templates to platform controllers. Tenant-controlled workload and VM schemas must not offer an alternate mount, exec, debug, token or log route into service-account material. An installation that cannot enforce those boundaries cannot claim strict secrecy from actors holding those capabilities.

## Per-engine adapters

### PostgreSQL

The chart moves the password and role attributes to CNPG `managed.roles`. The init path keeps role and database existence, object privileges, extensions and the removal of orphaned roles.

`ALTER ROLE ... PASSWORD 'SCRAM-SHA-256$...'` stores an already-encoded verifier as-is, and CNPG forwards a non-plaintext value unchanged rather than re-encoding it. Either path can therefore carry a verifier. The reason to choose `managed.roles` is the trigger: a mint that only changes a Secret starts nothing on the init path, because the Job is a `post-install,post-upgrade` hook and helm-controller does not render a release whose chart and values are unchanged. Forcing a reconcile to drive one password change re-runs the whole chart and every hook, and does not work on a suspended release.

The mint writes a per-user Secret in `kubernetes.io/basic-auth` form carrying the username and the verifier, labelled `cnpg.io/reload: "true"`. The operator watches it and the instance manager applies the value on the primary. `status.managedRolesStatus.passwordStatus[<role>].resourceVersion` then carries the resourceVersion of the Secret the operator actually applied, and comparing it with the Secret the mint wrote is the adapter's applied state. `transactionID` in the same status names the transaction that changed the role. No resident controller is needed for either.

Two consequences follow and are not hidden. The chart cannot create that Secret, because Sprig cannot compute a salted PBKDF2 verifier, so between application creation and the first mint the role exists and CNPG reports it unable to reconcile. And taking a role under `managed.roles` makes the operator authoritative for its attributes, so `inRoles` and the `user managed by helm` comment must be rendered into the spec from the same values the init script reads, or the operator will revoke the grants the script issued and erase the marker the script uses to find orphans.

A role whose password is NULL cannot authenticate by password. Whether any other authentication method inside the pod reaches it depends on the image's system users and is an acceptance check, not an assumption.

### MariaDB

The native `User` reference path stays: the operator is the sole applier of user passwords. After conversion the chart renders `User` with `passwordHashSecretKeyRef` pointing at a Secret the API owns and the chart no longer renders, which means the API is also what labels that Secret `k8s.mariadb.com/watch`. Before the first mint that Secret does not exist, and the reference points at nothing.

That matters more here than elsewhere. The CRD states that a `User` with no password reference is created locked with an expired password, so "declares existence without a password" is not a neutral state the account leaves on its own. Whether the operator clears the lock when the reference starts resolving, or the account stays locked after its first mint, decides whether the first mint alone produces a working login. Both branches are testable and neither is assumed here; the acceptance suite asks the question directly.

The same uncertainty runs the other way for deny-login. If the operator owns the lock attribute, an `ACCOUNT LOCK` applied out of band is reverted on its next pass, and deny-login has to be expressed where the operator reads it. The pinned operator's behaviour is an acceptance requirement before the capability is reported as supported.

MariaDB has no per-user applied-state field: `User` status carries conditions only, and changing the referenced Secret alone does not move the object's generation. The adapter therefore cannot prove which version reached the engine from status alone, and the record carries its MariaDB applied state as inferred rather than observed. An authentication probe is the only direct evidence, and the acceptance suite uses one.

MariaDB root is a service account: it moves to the service Secret, keeps its chart-side generation and its `rootPasswordSecretKeyRef`, and is never minted or revoked by this API. The CRD has no hash variant for it, so it stays plaintext by necessity.

### ClickHouse

The engine takes a verifier: the chart already sends only `password_sha256_hex` into the `ClickHouseInstallation`, so ClickHouse never sees a plaintext tenant password. What changes is where that hash comes from, and the delivery runs through Helm rather than around it.

The mint writes a per-release Secret holding a values fragment, `_userVerifiers`, keyed by account name. The HelmRelease lists that Secret in `valuesFrom` beside the platform values it already carries, and the Secret is labelled for Flux to watch. A mint therefore changes the release's values, helm-controller notices a new values digest and runs an upgrade, the rendered CHI differs in one key, the operator rewrites the users ConfigMap without restarting anything, the kubelet delivers the new content within about a minute, and ClickHouse re-reads `users.d` a couple of seconds later. The chart renders the hash with `required`, so an account whose verifier is missing fails the render rather than silently taking a default.

Three alternatives were examined and rejected. The operator watches no Secrets at all, so nothing reaches it by writing one directly; its old `k8s_secret_password_sha256_hex` reference was deprecated and is removed outright in 0.27.4, and the supported `secretKeyRef` form goes through an environment variable and restarts pods on every change. Patching the users section of the live CHI as a second writer does work under server-side apply, because the users map is merged by key, but it loses a race that matters: a chart render that adds an account before the patch lands hands that account the operator's default password. SQL-managed users are incompatible with the configuration-defined ones, would require the chart to stop rendering users entirely, and the operator's own SQL account is restricted by IP to the operator pod, so nothing else can use it.

The applied state is partial and stated as such. The CHI reports its reconcile complete with a hash of the normalized configuration, which proves the ConfigMap was written, not that a replica re-read it. An authentication probe is the only direct evidence, and the acceptance suite uses one.

The costs are real. Every mint is a Helm revision, which means the verifier appears in the release's stored values and in `helm get values`, and the release history holds a bounded number of them. A suspended or failed release cannot take an upgrade, so a mint against one is accepted and reported as pending rather than applied. The API has to create the verifier Secret before the HelmRelease exists, and update it before the HelmRelease that adds an account. And `users.<name>.password` has to leave the values in its own change, because this path does not remove it.

The hash is unsalted and single-round. For a password minted at the required length that is not a practical weakness; for a password a tenant chose it is, and the coverage table says so rather than treating the class as uniformly safe.

The `backup` account is a service account and moves to the service Secret, where the backup CronJob and the backup sidecar read it as they do today. That part does not depend on the delivery question and can land first.

The rendered user configuration materialises as a ConfigMap in the tenant's namespace, which is a fifth place the verifier exists. No tenant role grants `configmaps`, so nothing reaches it today, but the route belongs in the table rather than being left unmentioned.

### OpenSearch

Out of phase 1, because the problem here is not the verifier.

A tenant-declared OpenSearch user does not reach the engine at all today. The chart renders a `kubernetes.io/basic-auth` Secret per user, labelled `opensearch.opster.io/credentials`, and nothing consumes it. The operator creates users from `OpensearchUser` objects, which the chart never creates; the label the chart uses is not an import interface, and the annotations the operator's secret handler looks for are ones it writes itself while reconciling a custom resource that already exists. The chart's own tests assert the shape of the Secret, not the existence of an account. So the declared user has a password nobody can use, because the account was never created.

That has to be fixed before the class means anything, and fixing it is not a password change. Two shapes exist. Creating `OpensearchUser` objects makes the operator the writer, and that kind takes a plaintext reference with no hash field at all, so the class would be plaintext-by-operator like RabbitMQ rather than verifier-only. Rendering users into the security plugin's `internal_users.yml`, which does take a bcrypt hash, makes the chart the writer and bypasses the operator's user API; the two paths overwrite each other, so it is one or the other.

The securityconfig path also has a weak applied state. The operator runs a Job to load the configuration and compares a checksum to decide whether to run it again, and the loader's retry loop exits on a counter without returning an error, so a Job that never applied anything can still report success. Neither the cluster status nor the user status carries an applied version. Only an authentication probe proves anything here.

The admin account is separate and has its own problem. The operator reads it from its own Secret; the tenant-facing Secret holds a second copy, connection URI included, and the `ApplicationDefinition` and dashboard Role hand it to the tenant. The service-account split removes that copy, and removing it empties the tenant-facing Secret, because today it holds nothing else. It also does not un-disclose anything: a tenant who already read that password still knows it, so the migration has to rotate the admin credential and update whatever depends on it, not merely stop publishing it.

## Revocation per engine

Three end states, kept apart:

| Engine | Deny login | Remove account | Terminate sessions |
|---|---|---|---|
| PostgreSQL | `users.<u>.denyLogin` in application values, rendered to `login: false`; writing it into the engine or onto the live Cluster is reverted | Remove the key from `users`; `ensure: absent` drops without reassigning and fails silently | `pg_terminate_backend`, needs an executor |
| MariaDB | `ACCOUNT LOCK`; whether the operator owns this attribute is unestablished and decides whether an out-of-band lock survives | Delete the `User` | `KILL USER`, needs an executor |
| ClickHouse | Not offered by the configuration path | Remove the key from `users`, which stops rendering the user | `KILL QUERY` only, no socket-close guarantee, and no account exists to issue it |
| OpenSearch | Not offered by the internal users API | Delete the internal user | Cache flush is not a session close |
| Qdrant | Not offered; one shared API key | Replace the key, which affects every client | Restart on key change; no per-key disconnect |
| Redis, Valkey | ACL `off` | `ACL DELUSER`, except `default` | `CLIENT KILL USER` |
| RabbitMQ | Not in the `User` CRD; needs an HTTP caller | Delete the `User`, which the topology operator executes | Deleting the user closes its connections |
| MongoDB | Not found | `dropUser`, needs an executor | `killAllSessionsByPattern`, no TCP guarantee |
| Harbor | Disable for robots; not for the bootstrap admin | Delete through the API, needs a caller | Not established for issued tokens |

A capability the adapter cannot perform reports false and the operation is refused before any engine is touched. `terminateSessions` is false for every engine in phase 1.

Where an executor is needed it is a step inside the synchronous call or inside the existing Job, not a resident process. The credentials it uses are named per engine: the CNPG superuser Secret for PostgreSQL, the service Secret's root key for MariaDB. ClickHouse has no usable administrative account for this — the operator's own SQL user is restricted by IP to the operator's pod — so terminating a query there needs an account the chart does not create today. For Harbor and MongoDB the caller's credentials source is unresolved.

PostgreSQL shows why the deny has to reach the declaration rather than the engine. CNPG reconciles a role against its spec and reverts anything it finds different: a manual `NOLOGIN`, a manual `PASSWORD NULL`, a password changed by hand, a dropped role it recreates, even the role comment and its memberships. The reconcile runs on Cluster events with no periodic requeue configured, so how long a manual change survives is not something the code promises and has to be measured rather than assumed. Direct SQL is therefore useful for exactly one thing here, terminating sessions, and useless for everything that changes role state.

Patching the live Cluster does not work either. The chart's rendered `managed.roles` is applied as a two-way merge between the previous render and the new one, the live object taking no part, and a list is replaced wholesale whenever it differs at all. A patch written onto the live CR survives only until any user is edited, then disappears without a conflict to warn anyone. Coexistence is possible on disjoint fields, which is how the backup driver lives in `spec.plugins`, and impossible on `managed.roles`.

So the deny lives in the application's values, as a per-user `denyLogin` flag the chart renders into `login: false`. The values have no such field today and gain one with the adapter. Three writers already share application values — the tenant, the platform and the restore driver — and none of them owns a field, because an application update replaces values as a whole and carries no field ownership. The API therefore reasserts the flag from the account record on every write it makes, which is the mechanism the platform already uses to keep its shard label from being dropped by a tenant edit, and only `allowLogin` clears it.

The cost is real and worth stating plainly. Each deny and each release is a values change, which is a `helm upgrade`, which re-runs the init Job. A release that is suspended or already failed cannot take one, so deny-login is unavailable exactly when a release is unhealthy.

`disablePassword` is not used. CNPG's admission webhook refuses a role that carries both `disablePassword` and a `passwordSecret`, so a chart that rendered both would fail the whole release rather than the role, and rendering them mutually exclusive means the password reference disappears while the account is denied. `login: false` has no such constraint, keeps the verifier synchronised while the account is shut, and closes every authentication method rather than the password ones.

Removing an account is removing its key from `users`, not `ensure: absent`. CNPG's absent path issues a bare `DROP ROLE IF EXISTS` with no `REASSIGN OWNED`, so a role that owns objects fails silently into `cannotReconcile` while the account keeps working. Worse, leaving the key in `users` while asking CNPG to drop the role produces a loop: the init Job creates it, the operator drops it. Dropping the key instead makes CNPG ignore the role and leaves the removal to the init script, which does carry `REASSIGN OWNED`. That script has its own defect, recorded rather than inherited: it reassigns only inside the `postgres` database, so a role owning objects in a tenant database fails the Job and the release with it.

## Service account split

Per release, a second Secret holds the accounts the platform itself consumes. It is rendered by the chart with the same `lookup` plus random generation used today. Nothing else about its generation changes, so bootstrap ordering is exactly what it is today: MariaDB's datadir-time root read finds the Secret the chart rendered, and no admission gate or generator controller is introduced.

What keeps it out of tenant view is that no `ApplicationDefinition` selects it and no dashboard Role names it. The lineage webhook stamps `internal.cozystack.io/tenantresource=true` on what an `ApplicationDefinition` selector matches, and the dashboard grant is by `resourceNames`, so the service Secret simply appears in neither list. Some charts also select by an `apps.cozystack.io/user-secret` label, and the service Secret carries none, but that is a consequence rather than the mechanism.

The accounts that move: MariaDB `root`, the ClickHouse `backup` user, the OpenSearch admin copy in the tenant-facing Secret, Harbor's `redis-password`. The list is per engine and grows as adapters convert.

Moving an account is not the same as un-disclosing it. Every one of these was readable by the tenant until the split, so the account is rotated as part of the same migration, and whatever consumes it is updated in the same step. The OpenSearch admin is the sharpest case, because the tenant was handed a working connection URI, but the rule holds for all of them.

The exposure that remains is stated: this Secret is rendered by a chart, so its plaintext enters Helm release history like any other rendered Secret. For tenant accounts the history route closes because the chart stops rendering their material; for service accounts it stays open, and the route table says so per class rather than claiming a uniform guarantee.

This split closes [cozystack/cozystack#4164](https://github.com/cozystack/cozystack/issues/4164) adapter by adapter, not all at once. The issue is about ancestor ServiceAccounts holding a name grant on the credentials Secret. For a converted class that Secret holds only verifiers and the plaintext has moved to an object nothing grants; for an unconverted class it still holds the plaintext and the grant still reaches it. The issue closes when the last class the grant covers is converted or classified.

## Migration

Three stages, run as a numbered platform migration rather than by a resident controller.

**Stage A, preparation.** Annotate the tenant credentials Secret with `helm.sh/resource-policy: keep`, and create the service Secret with the service accounts copied into it. No authentication changes and nothing is removed. Without the annotation, the chart change in stage C deletes the Secret along with whatever stage B wrote into it, because Helm removes an object a template stopped rendering unless that annotation says otherwise.

**Stage B, verifiers.** Compute verifiers from the plaintext the platform already holds and write them where each adapter specifies. For PostgreSQL the target is a new per-user Secret and nothing existing is touched. For an adapter whose target is the existing Secret, the verifier goes under a new key alongside the plaintext one, never over it: the unconverted chart and the unconverted operator still read the old key, and a hash written there is applied as if it were a password. Authentication is unchanged at the end of this stage too.

**Stage C, the flip.** The charts stop rendering tenant material and start pointing at the verifier, and the migration takes field ownership with the forced server-side apply the platform's existing migrations use. Helm does not release `managedFields` ownership when a template stops rendering a field, so the transfer is performed rather than awaited. The chart change and the transfer ship in the same release, because a chart that still renders the credentials Secret and an API that writes it are two writers of one object. Every account converted here is marked previously exposed until re-minted.

The previously-exposed marking survives every stage and every rollback. It is cleared only by a mint, and a mint clears it only for the account minted.

A deadline per adapter follows the adapter, never precedes it. What enforces the deadline is admission in the aggregated server against the per-account records, which is code in the API server rather than part of the migration script.

## Restore and lifecycle

A restored database carries the passwords its backup captured, and those are not the source's current passwords. A password minted, disclosed, then replaced at the source is still live in the backup, so a restore hands a working credential to whoever held the old one. Restoring and publishing without touching credentials is therefore not safe, whatever the record says.

The restore path mints fresh credentials for every account of the restored application before the destination is reachable by anything but the restore driver, and every restored account carries the previously-exposed marking until that mint completes. The driver is the backup controller, which already patches the application it restores; creating the per-adapter material and the records is part of the same path rather than a new component. Where the source is gone and no record survives, the accounts are treated as exposed, because the absence of evidence about a backup's age is not evidence of freshness.

The existing restore path has a defect this proposal does not fix but must not build on: `bootstrap.enabled` is never reset after a recovery, the init Job is skipped for a recovered application, and the chart still renders a credentials Secret holding passwords that were never applied to the database. Under `managed.roles` the operator applies from the per-user Secrets once a primary exists, so the restore path must create those Secrets before publishing the destination.

Whole-platform recovery from an old control-plane snapshot is an explicit offline procedure: isolate engines and tenant API access, mint fresh credentials for every account the snapshot restored, and only then reopen service. A snapshot can restore a record that says an account was never exposed while the engine still holds a password that was.

Deleting an application closes its records rather than removing them, on the retention in [operating limits](#operating-limits). The aggregated API server does this as part of serving the deletion. An application deleted through its HelmRelease directly, outside the aggregated API, leaves records whose application is gone; those are found by the same reconciliation that enforces migration deadlines and are closed there, and until then they report as orphaned rather than disappearing quietly.

## Acceptance

No live engine, migration or browser test is claimed as executed by this proposal. Each phase runs the suites applicable to its agreed adapters.

**Per adapter.** A minted password authenticates. The old password is rejected. Roles, grants and object privileges are unchanged. The applied state appears on the record within the adapter's interval. A mint that stalls after claiming a version, while a second mint completes, writes nothing when it resumes.

**Revocation.** Deny-login denies and survives the operator's next reconcile. A mint against a denied account changes the password and leaves the deny standing; `allowLogin` lifts it. Account removal removes, and the account does not reappear until the application declares it again. Session termination, where a capability claims it, ends an established session.

**PostgreSQL specifics.** `managed.roles` applies a SCRAM verifier to a role the init path created. The reload label triggers application, and the delay is measured. `passwordStatus[<role>].resourceVersion` matches the Secret the mint wrote. The orphan-cleanup loop in the init script does not delete a managed role. A role with a NULL password cannot authenticate from outside the pod. The Cluster's readiness while a role waits for its first mint is observed rather than assumed. `denyLogin` written to values survives a tenant edit of an unrelated field, because the API reasserts it, and how long a hand-made `NOLOGIN` lasts before CNPG reverts it is measured rather than assumed. Removing a `users` key drops a role that owns objects in a tenant database without failing the Job, once the init script is fixed.

**MariaDB specifics.** The pinned operator re-applies `passwordHashSecretKeyRef` when the labelled Secret changes. A `User` declared without a password reference is created locked, and the first mint that supplies the reference produces a login that works — or does not, in which case the adapter needs a lock-clearing step before the capability is reported. `ACCOUNT LOCK` applied out of band survives an unchanged application reapply and an operator restart, or it does not, and either answer decides where deny-login lives for this engine.

**ClickHouse specifics.** A mint produces a Helm revision whose rendered CHI differs in one key, and the operator rewrites the users ConfigMap without restarting a pod. The end-to-end delay from mint to a working login is measured. An account whose verifier is absent fails the render rather than receiving a default password. A mint against a suspended release is reported as pending and applies when the release resumes.

**Isolation.** Guessed names, forged labels, manipulated selectors, sibling and ancestor references, raw and projected reads, and privileged pod templates. Assert neither disclosure nor unauthorized use. The service Secret is never served through `tenantsecrets` and never appears in a dashboard resource map. The ClickHouse users ConfigMap is not readable by any tenant role.

**Migration.** Each stage on a populated cluster with every first-wave engine. After stage A, a chart upgrade that would have removed the credentials Secret leaves it in place. After stage B, authentication with the existing passwords still works. After stage C, a chart reapply does not reintroduce rendered material, and field ownership sits with the migration. Rollback between each pair of stages, and a rollback to a pre-conversion revision, which is confirmed to restore the old plaintext and to leave the previously-exposed marking standing.

**Audit.** A mint call produces a kube-apiserver audit event naming the principal, the resource, the subresource and the response code, with no body. The record carries the same initiating identity. Both are checked on the pinned distribution, and an installation without an audit policy is reported as unable to claim strict mode.

## Alternatives considered

**Keep generation in Helm or in a template helper.** Rendered material enters release history and `lookup` is not an observed dependency. Rejected for tenant accounts and accepted for service accounts, where the consumer is a machine, the Secret is not tenant-facing, and the alternative is a new writer for material nobody outside the platform reads.

**Keep one-time collection with an entitlement, a window and a consumption state.** Every part of it exists to guard plaintext the platform holds. With verifier-only storage there is nothing to collect twice, so the entitlement has no subject. For the classes that still hold plaintext, disclosure control is a policy on the tenant-facing interface, and the coverage table says that per row instead of promising it globally.

**A durable journal with a transactional outbox, an exporter and a fail-closed threshold.** The apiserver's Metadata-level audit names the principal and the object and never the body, and Cozystack collects it by default. Its gaps are addressing by credential, a tenant-readable path and engine outcome; the per-account record covers those three at a fraction of the cost.

**A subresource with no durable record at all.** Consumption state and ordering do disappear with verifier-only storage. Account identity does not. An Application reports its HelmRelease's UID, which a recreated application does not keep, so nothing derived from the live objects distinguishes an account from its successor, and the previously-exposed marking would land on the wrong one or on nothing. An annotation on the Secret does not survive the chart recreating that object either. A record keyed by the account rather than by the release is what carries the history across a recreate, at the cost of retaining records for applications that no longer exist.

**Five kinds with operations, bindings and an event kind.** Operations and events manage state that verifier-only storage removes. Delivery of credentials into a tenant's managed cluster is a separate concern that belongs with a store rather than with minting, and nothing here replaces it.

**Return values only from create, or allow replay until acknowledgment.** Adopted. The objection had two halves: asynchronous issuers do not fit a synchronous response, which is why COSI is not in phase 1, and unattended creation does not fit, which is why declaring an account no longer generates a password. Replay stays refused because it releases the same bytes twice.

**SQL Jobs for every engine, including MariaDB users.** Removing the native reference creates a bootstrap gap and two writers, as [#71](https://github.com/cozystack/community/issues/71) worked through. MariaDB keeps its native writer; a Job is used only where an engine offers no observable path.

**Rotate maintenance root in the same operation as a tenant account.** That changes the authority needed to recover the operation itself. Service accounts keep their own lifecycle with the engine maintainers.

**Require Vault or KMS, or promise that no component can recover a plaintext.** For verifier classes the platform already holds nothing after the response returns, which is checkable. For plaintext classes external custody does not remove delivering bytes to the engine, and those classes are named in the coverage table and go to [#82](https://github.com/cozystack/community/pull/82).

---

<!-- Inspired by KubeVirt enhancement proposals and Kubernetes Enhancement Proposals (KEPs). -->
