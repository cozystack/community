# User secrets API: platform-generated credentials, minted and shown once

- **Title:** `User secrets API: platform-generated credentials minted on request, shown once, stored as verifiers`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-09-28`
- **Status:** Review

## Overview

Cozystack generates the passwords of managed application accounts at Helm render time and keeps them in a `<release>-credentials` Secret that tenants can read again at any time. A deployment whose security policy requires that a generated credential is shown exactly once, at creation or regeneration, with regeneration as the only way back and every issuance audited, cannot be served by that model whatever the dashboard does.

This proposal moves generation into the Cozystack API. A `mint` call on a per-account `Credential` generates a password, stores only what the engine needs to check it, and returns the plaintext in the response body. Nothing is left to read a second time, and a lost response is answered by minting again, which replaces the password. For PostgreSQL, MariaDB and ClickHouse in the first wave, which store a password verifier, this follows from what is stored. The other engines get the same mint in later waves with the plaintext kept where only the platform can read it, and those that accept a verifier move to one afterwards, as separate work. The platform's own accounts, which share those Secrets today, stop being visible to tenants. Attribution comes from the kube-apiserver audit log that Cozystack already collects, and a small per-account record shows tenants where the live password came from, who changed it recently and whether the engine applied it.

An application kind counts as converted once its chart works this way, and each kind converts on its own (§7). [`example.md`](./example.md) walks one PostgreSQL application through the whole cycle with the objects at each step.

## Scope and related proposals

- [Tenant-supplied secrets by reference](https://github.com/cozystack/community/pull/82) owns the secrets a tenant brings, and this proposal the ones the platform generates. No generated field gets a reference form here, and a later change that adds one keeps it off by default. There is no write path on `tenantsecrets` either: a field that needs a tenant-supplied value stays inline until the reference capability exists. The write-only `TenantSecret` of the [earlier revision](https://github.com/cozystack/community/pull/74) of this proposal is withdrawn in favour of #82.
- It supersedes [managed database password rotation via a controller](https://github.com/cozystack/community/pull/72). Rotation here is the mint call and needs no controller.
- It builds on [cozystack/cozystack#4078](https://github.com/cozystack/cozystack/pull/4078), merged on 2026-09-25, which removed `users[].password` from the postgres and mariadb charts and took the postgres password out of the init-script.
- It closes [cozystack/cozystack#4164](https://github.com/cozystack/cozystack/issues/4164) for the credentials of converted kinds: the name grants that give every ancestor tenant's ServiceAccount a child's passwords go away.
- It takes nothing from [Unified TLS and PKI](../unified-tls-pki/README.md). Under that proposal's rule no engine's key-bearing Secret is tenant-facing, and the trust anchor it delivers is public.
- Kubernetes access credentials stay at their CA, and VM `cloudInit` is a named gap. Both are classified in §7 and not converted.

## Decisions

<!-- Leave this empty in the initial PR; fill it in as implementation
proceeds. Records live in this proposal's own directory, numbered from
0001. Link each one here, newest first:

- [0001. Short statement of what was decided](./decisions/0001-short-slug.md) — one clause on what it settled.

Where implementation changed the design, the record is what explains why
— this section is how a reader of the proposal finds that out.
See ../README.md#decision-records. -->

## Context

Baseline: cozystack/cozystack at `29932ddbb` (2026-09-28). Paths are in that repository unless stated.

The postgres, mariadb and clickhouse charts generate passwords at render time with `randAlphaNum 16`, keep them across reconciles with `lookup`, and store them in a chart-rendered `<release>-credentials` Secret (`packages/apps/postgres/templates/init-script.yaml:1-8,99-124`, `packages/apps/mariadb/templates/secret.yaml:88-122`, `packages/apps/clickhouse/templates/clickhouse.yaml:2,21-28`). Since cozystack/cozystack#4078 the postgres init-job reads the password from that Secret at run time (`init-script.yaml:187-195`) instead of carrying it in the init-script. Five routes lead to the password. Two keep a copy of it, two open the Secret that holds it, and one keeps its hashes:

1. The application spec keeps a copy. cozystack/cozystack#4078 removed `users[].password` from postgres and mariadb, while ClickHouse and OpenSearch still take `users.<name>.password` (`packages/apps/clickhouse/values.yaml:97`, `packages/apps/opensearch/values.yaml:215`), which every subject that can get the application reads.
2. `tenantsecrets` opens the Secret. The lineage webhook labels a Secret `internal.cozystack.io/tenantresource=true` when the owning application's ApplicationDefinition selects it (`internal/lineagecontrollerwebhook/webhook.go:182-191`), and `core.cozystack.io/tenantsecrets` serves the whole `data` of such a Secret (`pkg/registry/core/tenantsecret/rest.go:101-120`) to every tier from `use` up (`packages/system/cozystack-basics/templates/clusterroles.yaml:209-214`). All four database definitions select `<release>-credentials`. The webhook decides once, at first admission, and skips the object afterwards (`packages/system/lineage-controller-webhook/templates/mutatingwebhookconfiguration.yaml:43-46`), so editing a definition does not relabel existing Secrets.
3. Name grants open the Secret. Each chart's `<release>-dashboard-resources` Role grants `get` on `<release>-credentials` (e.g. `packages/apps/postgres/templates/dashboard-resourcemap.yaml:16-22`) to the `use` groups and above and to the ServiceAccount of the tenant and every ancestor up to `tenant-root` (`packages/library/cozy-lib/templates/_rbac.tpl:84-96`). This is cozystack/cozystack#4164.
4. Helm history keeps a copy. Every stored revision of the release carries the Secret's plaintext, up to `MaxHistory`, 5 by default (`pkg/registry/apps/application/rest.go:1685,1706`), and a `helm rollback` restores it.
5. The ClickHouse operator keeps hashes in the `chi-<chi>-common-usersd` ConfigMap in the tenant namespace, on which no tier holds a verb.

The platform is its own client through the same Secrets. MariaDB `root` shares `<release>-credentials` with tenant users (`secret.yaml:88-90`), and mariadb-operator logs in with it on every SQL reconcile. ClickHouse adds a `backup` account to every instance, backups enabled or not (`clickhouse.yaml:4-5`), and keeps its password there for the backup jobs. OpenSearch's `<release>-credentials` is a copy of its operator's admin account, and Harbor's carries the password of its internal Redis.

The engines already store verifiers: SCRAM-SHA-256 in PostgreSQL, a `mysql_native_password` hash in MariaDB, SHA-256 in ClickHouse, whose chart renders nothing else into the CHI, and bcrypt in OpenSearch. None of them needs the plaintext once it is set.

Audit exists outside the Cozystack API, which runs without audit flags (`packages/system/cozystack-api/templates/deployment.yaml:31-33`). On a Talos cluster running a 1.7.0-alpha.1 build, kube-apiserver recorded a request for a subresource of a `tenantsecrets` object that does not exist with the resource, namespace, name and subresource in `objectRef`, next to the user, groups, source IP, response code and authorization decision. monitoring-agents, a default package, ships that log and Kubernetes Events to VictoriaLogs (`packages/system/monitoring-agents/values.yaml:349-358,389-410`), kept for a month by default. The user it names is a person only with OIDC (off by default, `packages/core/platform/values.yaml:471-473`), under which the dashboard passes the user's ID token through (`packages/system/dashboard/templates/gatekeeper.yaml:72-75`). Without OIDC the dashboard signs in with a pasted token, in practice the token of the tenant ServiceAccount.

### The problem

- A tenant creates a database and the platform generates its passwords. From then on every `use` member of the tenant and the ServiceAccount of every ancestor tenant can read them at any time, and nothing a tenant can see records who did.
- A leaked password has no platform path to rotation. A change made by hand inside the database is undone on the next run of the init-job or the operator, and a rotation in the chart would keep the old value in Helm history.
- The platform's own accounts (MariaDB `root`, the ClickHouse `backup` user, the OpenSearch admin) are shown to tenants like their own accounts. Tenants should never see them.
- A deployment that must show a generated credential once cannot offer managed databases at all.

## Goals

A converted kind is an application kind whose chart has moved to this design (§7).

- For every account of a converted kind the platform generates the password, with one generator for all engines (§2), and no application field sets one.
- The plaintext is returned once, in the response of the mint that generated it. Afterwards no Cozystack component or Kubernetes object holds it, except, for a kind that stores the plaintext (§7), its account Secret, which no tenant can read.
- A mint replaces the previous password, which the engine rejects once it has applied the new one.
- Every mint and revoke leaves a kube-apiserver audit event naming the caller, the time and the account, and the account's record shows the recent ones.
- A minted value appears in no log, Event, condition, error message or audit record.
- A tenant subject without the mint verb sees account metadata only, and no tenant subject can read a platform account's password in a converted kind.

### Non-goals

- Secrets the tenant supplies, which are #82.
- Delivering a minted credential into a tenant's Kubernetes cluster or VM. The caller stores it.
- TLS issuance and trust anchors, and retiring the static admin kubeconfig of managed Kubernetes.
- Closing connections that are already open when a password changes.
- Proving before the response that the new password works (§3 says why).
- Kafka users with topic ACLs, which the Kafka chart does not have yet (§7).
- Moving the wave-2 engines from stored plaintext to verifiers, which follows as separate work per engine (§7).
- Protection from management-cluster administrators, Flux or the operators, which hold every tenant's credentials today.

## Design

### 1. The account record

Each account of a converted application is one `Credential` in the tenant namespace, served by the Cozystack API in `core.cozystack.io/v1alpha1`:

```yaml
apiVersion: core.cozystack.io/v1alpha1
kind: Credential
metadata:
  name: postgres-orders.web
  namespace: tenant-acme
  uid: 3f9c2e1a-5b7d-4c0e-9a61-0d2f8e4b7c35   # the account's identity
  resourceVersion: "48213"
spec:                                         # derived from the application, read-only
  application: {apiGroup: apps.cozystack.io, kind: Postgres, name: orders}
  user: web
status:
  password:                                   # where the live password came from
    origin: Minted                            # NotIssued | Migrated | Minted | Revoked
    at: "2026-10-02T09:14:07Z"
    by: jane@example.org
  history:                                    # the last ten operations, newest first
  - {operation: Mint, at: "2026-10-02T09:14:07Z", by: jane@example.org}
  - {operation: Migrate, at: "2026-09-30T02:11:40Z", by: "system:serviceaccount:cozy-system:cozystack-migration-hook"}
  engine: {state: Applied, observedAt: "2026-10-02T09:14:09Z"}   # Applied | Pending | Failed | Unknown
```

It is a view with no storage of its own. It lists the users the application declares in `users` and shows, for each, the record kept on its account Secret. The account Secret:

- lives in the tenant namespace, named `<release>.<user>.account`, where `<user>` is the user name lowercased with every character outside `[a-z0-9-]` replaced by `-`, and only the Cozystack API writes it
- holds the original `username` and, under `password`, the value the engine checks passwords against (§3)
- keeps the record in its annotations
- is owned by the application's HelmRelease and carries the label the engine's operator watches

The view never shows the Secret's data, no ApplicationDefinition selects the Secret, and no Role grants it.

`status.password.origin` says who can know the live password:

- `NotIssued`: the account was created with a random password that the API threw away at once. Nobody has ever received a password for it, and nobody can log in until someone mints.
- `Migrated`: the account went through the conversion with the password tenants could read before it. That password still works and should be replaced by a mint.
- `Minted`: the password was shown once, in the response to the mint that `by` made at `at`. Nobody else received it.
- `Revoked`: `by` revoked the password at `at`. The live value is random and was thrown away, so nobody holds a working password until the next mint.

`status.history` keeps the last ten operations (`Create`, `Migrate`, `Mint`, `Revoke`), so a tenant, who cannot read the audit log, sees who changed the password and when, while the audit log keeps every one. `status.engine` is described in §3.

The identity of an account is its Secret's UID. It stays the same across reconciles, upgrades, in-place restores and the conversion. When the application is deleted and created again under the same name, it gets new Secrets, and a Secret left over from the deleted one is never reused.

The Cozystack API keeps account Secrets in step with `users` whenever it creates or updates an application of a converted kind, whichever client sent the request (the dashboard, `kubectl`, an external API consumer):

1. For each user in `users` without an account Secret, or with one that is not theirs (left from a deleted application of the same name, or holding another `username`), it writes a new Secret with the verifier of a random password that it throws away, origin `NotIssued`. The user then exists in the database, and nobody can log in as it until someone mints.
2. It writes the HelmRelease. On create, the Secrets from step 1 get the new HelmRelease as their owner right after.
3. It deletes the account Secrets of users that are no longer in `users`.

Two users whose names map to the same object name, or to one that is not a valid DNS name, are refused.

### 2. Mint and revoke

Every password is 32 characters drawn uniformly from `[A-Za-z0-9]` by `crypto/rand`, for every engine. That gives about 190 bits of entropy, above the 128-bit security strength NIST SP 800-57 asks of keys used beyond 2030, so even the unsalted hashes ClickHouse and MariaDB store (§3) cannot be brute-forced, and the alphabet needs no escaping in SQL, connection URIs or shells. Today's charts draw 16 characters from the same alphabet.

```
POST /apis/core.cozystack.io/v1alpha1/namespaces/tenant-acme/credentials/postgres-orders.web/mint
{"apiVersion": "core.cozystack.io/v1alpha1", "kind": "CredentialRequest",
 "spec": {"preconditions": {"resourceVersion": "48213"}}}

201 Created
{"apiVersion": "core.cozystack.io/v1alpha1", "kind": "CredentialRequest",
 "status": {"username": "web", "password": "<shown once>", "issuedAt": "2026-10-02T09:14:07Z",
            "engine": {"state": "Pending"}}}
```

This is the pattern of Kubernetes `TokenRequest`: a `create` on a subresource of the object, with a request kind of its own as the body, answered by the same kind with the result in `status` and nothing stored. A mint does four things:

1. Generates a password.
2. Computes what the engine stores: its verifier, or for a kind that stores the plaintext (§7) the plaintext itself.
3. Writes that value and the updated record into the account Secret, in one update that fails if the Secret changed since the caller read it.
4. Returns the plaintext in the response.

The plaintext exists in the API server's memory for the length of the request and in the response. After a second mint the first password is dead: unlike a token, which is added next to the others, a password is replaced. A mint on a declared user whose Secret is missing recreates it with a new identity, one on an undeclared user answers NotFound, and one on a user whose name maps to another declared user's answers Conflict.

`revoke` takes the same body, writes into the account Secret the verifier of a new random password, throws that password away and returns nothing secret, so nobody holds a working password. It retires a leaked password when nobody should get a new one yet. Neither call lets the caller choose the password.

A lost response (a closed tab, a dropped connection, a crash between the write and the answer) leaves a password nobody holds. The record shows the issuance and its initiator, and the way forward is another mint.

Two concurrent mints cannot both succeed: the conditional write lets one land, and the other gets `409 Conflict` without a password. A caller can also pass the `resourceVersion` it last read, and the dashboard always does. If someone minted in between, the caller gets a 409 showing the newer issuance instead of silently killing the password another person just received.

### 3. Where the stored value goes

CNPG wants one Secret per role, mariadb-operator a key reference per `User`, and the ClickHouse operator watches no Secrets, so each chart wires its engine to the account Secret in its own way:

| Engine, vendored operator | Wiring | Stored value | `status.engine` is Applied when |
|---|---|---|---|
| PostgreSQL, CNPG 1.30.0 | `Cluster.spec.managed.roles[].passwordSecret`, Secret labelled `cnpg.io/reload: "true"` | `SCRAM-SHA-256$<iterations>:<salt>$<StoredKey>:<ServerKey>` | CNPG reports the account Secret's `resourceVersion` as applied for the role |
| MariaDB, mariadb-operator 25.10.2 | `User.spec.passwordHashSecretKeyRef`, Secret labelled `k8s.mariadb.com/watch` | `PASSWORD()` output, `*` and 40 hex digits | never: `Unknown` while the `User` is Ready, `Failed` when it is not |
| ClickHouse, clickhouse-operator 0.25.2 | HelmRelease `valuesFrom`, Secret labelled `reconcile.fluxcd.io/watch: Enabled`, rendered by the chart as `password_sha256_hex` | unsalted SHA-256, hex | the release upgraded after the issuance and the CHI reconcile completed on every host |

#### PostgreSQL

Tenant accounts move under CNPG's declarative role management (`spec.managed.roles`) together with the per-database `<db>_admin` and `<db>_readonly` roles. The database roles come first in the list, because a new role receives its `inRoles` inside its own `CREATE ROLE`, so the roles it joins must exist already.

The chart renders every role as the init-script creates it today, because CNPG reverts any attribute that differs, revokes parent roles missing from `inRoles` and drops a comment the spec leaves unset, while the init-script finds the roles to remove by those comments:

- a database role gets `login: false`, `inherit: false` and the comment `role managed by helm`
- an account gets `login: true`, its `replication` flag, the comment `user managed by helm`, `inRoles` from `databases.*.roles`, and `passwordSecret` naming the account Secret

The init-script stops setting passwords and memberships, where it would be a second writer. It still creates the database roles, idempotently, because its own `ALTER DATABASE ... OWNER` needs them, and it keeps databases, ownership, privileges, extensions and the removal of undeclared users and roles.

A changed account Secret reaches the engine without Helm: the operator watches Secrets with the reload label, and the instance manager on the primary runs `ALTER ROLE ... PASSWORD` with the verifier, which PostgreSQL stores as it is. The API has to produce the canonical form exactly, since CNPG hashes anything else again as plaintext and the login then fails with no error. With the account Secret missing, CNPG skips every change to that role, `login: false` included.

#### MariaDB

Each `User` swaps `passwordSecretKeyRef` for `passwordHashSecretKeyRef` pointing at the account Secret, which carries the `k8s.mariadb.com/watch` label cozystack/cozystack#4078 already put on `<release>-credentials`. With the label, a change to the Secret makes the operator re-run `ALTER USER`. Without it the change waits for the SQL requeue, ten hours by default. A `User` created without any credential reference is locked for good (`ACCOUNT LOCK PASSWORD EXPIRE`, never unlocked), so every `User` carries the hash reference from its first reconcile. The operator pastes the hash into SQL as it is, which is why only the API computes and writes it.

#### ClickHouse

ClickHouse has no route from a Secret to the engine without a Helm run, so a ClickHouse mint costs a release upgrade and waits while the release is suspended. The operator watches no Secrets, `valueFrom.secretKeyRef` needs a pod restart per change, and `k8s_secret_password_sha256_hex` aborts the reconcile from operator 0.27.4. The one path that applies live is a hash inline in the CHI: the operator rewrites the `chi-<chi>-common-usersd` ConfigMap without a restart, and ClickHouse re-reads `users.d` every two seconds.

The chart already renders that hash. After conversion it takes it from values that the HelmRelease pulls from the account Secrets, through one `valuesFrom` entry per account. Both the API (`rest.go:1721-1726`) and the ApplicationDefinition reconciler (`internal/controller/applicationdefinition_helmreconciler.go:148-156`) pin `valuesFrom` to `cozystack-values` today, so both compute the new list with one function, and the reconciler writes it onto existing releases. Each entry reads the Secret's `password` into `_accounts.u<hex>`, where `<hex>` is the SHA-256 of the user name as `users` lists it, and the chart looks each declared user up by the same digest. A user name fits neither the `targetPath` nor the value (a comma fails the entry), while a hex digest fits both and leaves no order to keep aligned. The chart's own check is on its values: it fails the render when a declared account has no verifier in them, as a HelmRelease written around the API leaves it, because the operator would give such an account the password `default`, and it refuses tenant accounts named `backup`, `default` or `clickhouse_operator`.

#### A declared account without a Secret

A chart cannot see at render time whether a Secret exists except through `lookup`, which the Flux digest does not see and which this proposal removes for tenant accounts. The engines refuse on their own: CNPG leaves the role as it is and reports `cannotReconcile`, mariadb-operator sets the `User` to `Ready=False` and leaves the SQL user untouched, and Flux stops a ClickHouse release with `ValuesError`, which keeps its previous revision.

#### How the engine state is known

The Cozystack API computes `status.engine` each time a Credential is read, from what the engine's operator reports. Neither the plaintext nor a connection to the database is involved. For PostgreSQL the check is exact per role. For ClickHouse it proves the configuration was written, not that every replica reloaded it. MariaDB reports nothing per account: `UserStatus` has only conditions, and a Secret change moves neither the `User`'s generation nor its `Ready` condition, so the API cannot tell when a new password took effect. Two other ways were considered:

- Reading back what the engine stored and comparing it with the account Secret (`pg_authid` in PostgreSQL, `mysql.global_priv` in MariaDB). No plaintext is involved, but a platform component needs a privileged connection into every tenant database. It is the one way to give MariaDB a real signal (see Open questions).
- Logging in with the new password before answering. The API would need network access to every tenant database and would hold the plaintext until the engine converges, which for ClickHouse takes a Helm upgrade. Rejected.

### 4. Platform accounts

The platform's own accounts inside an engine keep their plaintext where their consumer needs it. Every such account stays in the `<release>-credentials` it lives in today, the tenant accounts move out of that Secret, and the Secret stops being visible to tenants: no ApplicationDefinition selects it, no Role grants it, and the chart renders it with `internal.cozystack.io/tenantresource: "false"`. The same rule holds in every engine because MariaDB leaves no other choice: `rootPasswordSecretKeyRef` cannot change once set. These passwords stay chart-generated with `lookup` and keep their plaintext in Helm history, which only management-cluster administrators and platform components read.

The conversion rotates every platform password tenants could read:

| Account | Consumer | Rotation |
|---|---|---|
| MariaDB `root` | mariadb-operator, which logs in with it on every SQL reconcile | the Job stores the new value in a platform-only Secret, runs `ALTER USER` with the current root password, then writes it into `<release>-credentials`, and a retry tries both passwords |
| ClickHouse `backup` | the backup CronJob, and the backup sidecar through its environment | the Job writes a new value and forces a release upgrade, which applies its hash and restarts the ClickHouse pods once for the sidecar |
| OpenSearch admin | the operator reads `<release>-admin-credentials`, and `<release>-credentials` holds a copy | with the OpenSearch conversion, in wave 2 |
| Harbor Redis | Flux, through `valuesFrom` | with the Harbor conversion, when its admin password moves out |

Removing the grants and selector entries closes cozystack/cozystack#4164 for converted kinds.

### 5. Who may do what

| Subject | get, list `credentials` | create `credentials/mint`, `credentials/revoke` |
|---|---|---|
| `view` | yes | no |
| `use` | yes | no |
| `admin`, `super-admin` | yes | yes |
| tenant ServiceAccount (`cozy:tenant`) | yes | yes |

Minting goes with the tiers that can change the application (`clusterroles.yaml:253-284`), because it changes a running service: every client holding the old password loses access. `use` members, who read `<release>-credentials` today, keep account metadata and connection details and lose the password. The tenant ServiceAccount already holds every verb on the tenant's applications, so automation that provisions an application can mint in the same run and store the response itself, which the walkthrough shows together with Terraform ([headless provisioning](./example.md#headless-provisioning-and-terraform)).

Ancestors inherit, since the tier and ServiceAccount bindings in a tenant namespace include every ancestor (`packages/apps/tenant/templates/tenant.yaml:8-87`). A parent administrator can mint a child's credential, as it can delete the child's database today, but the access is now visible: the mint replaces the password, the child's record shows who issued it, and the audit event names the actor.

`mint` and `revoke` are separate subresources because the audit log holds no bodies and tells them apart only by subresource, and because a custom role can grant `revoke` alone to an incident responder. By default both go to `admin` and the tenant ServiceAccount. `credentials` has no `watch`: the view is built from the application, the account Secret and the engine's object, and a watch would have to merge the three, so a client reads `status.engine` with `get` after a mint. It is namespaced with no cluster-wide list, like `tenantsecrets`, and a list reads from the API's caches, one pass over the accounts of the namespace, at the cost of the informers for the engine objects (Security). Its `resourceVersion` is the account Secret's, and the only use is the precondition of a mint.

Without OIDC the dashboard signs in with the tenant ServiceAccount token and the tiers collapse into the ServiceAccount, so a deployment that needs role-based disclosure and per-person attribution enables OIDC. An external API consumer maps its roles onto these tiers and, acting for a person, passes that person's OIDC token or uses Kubernetes impersonation, which the audit event records next to the caller.

### 6. Audit and service history

A mint is a `create` on `credentials/<name>/mint`, which kube-apiserver audits before proxying it to the Cozystack API, with the account in `objectRef` and the subresource telling a mint from a revoke. kube-apiserver records neither the body nor the response of a request it proxies to an aggregated API, at `Request` and at `RequestResponse` alike (run on kube-apiserver 1.34 and 1.37, see Testing), so the password cannot reach that log. That answers who, when and which account for every issuance, and Cozystack already ships the log to VictoriaLogs.

The one server that could write the password down is the Cozystack API, if it got an audit policy, and any such policy must keep `credentials` below `RequestResponse`. Distributions that give kube-apiserver no audit policy record nothing, so the installation documentation gets one for them, with the log path monitoring-agents tails. An external SIEM, the log and alerting system a security team runs, has no way in today: the fluent-bit outputs of monitoring-agents are one fixed string (`packages/system/monitoring-agents/values.yaml:377-410`), and overriding it drops the VictoriaLogs outputs. monitoring-agents gets a value for additional outputs.

A rotation is subscribed to on the audit log, in the SIEM or in a VMAlert rule group over VictoriaLogs that the operator adds, since Cozystack ships no log-based alert rules. Each mint and revoke also emits a Kubernetes Event on the application, naming the account and the initiator and never a value. Events are best-effort, because the client drops and merges repeated events about one object, and the API needs `create` on `events` for them. Only `tenant-root`, whose log store receives the cluster's audit log, can read that log, and every other tenant reads the record's history.

### 7. Coverage

A kind joins a wave once its whole path from mint to engine works, not once the engine could accept a verifier. Every wave uses the same `Credential` and mint. In wave 1 the account Secret holds only the verifier, and one-time disclosure follows from storage. In waves 2 and 3 the account Secret holds the plaintext that the engine or its operator reads. No tenant can read that Secret, so one-time disclosure holds on the tenant-facing interface, while platform components and management-cluster administrators can still read it.

A kind gives the API two things. Its tenant accounts are the keys of `spec.users`, with the name rules of §1 and no `password` field. Any other account, a platform one or the single credential of a kind with no `users`, is declared in its ApplicationDefinition. An out-of-tree kind provides the same two things, and the API serves it once its chart artifact is the converted one.

| Kind | Engine takes a verifier | What the tenant gets today | Wave |
|---|---|---|---|
| PostgreSQL | yes, SCRAM-SHA-256 | `<release>-credentials` | 1 |
| MariaDB | yes, `mysql_native_password` hash | `<release>-credentials`, `root` included | 1 |
| ClickHouse | yes, SHA-256 | `<release>-credentials`, `backup` included | 1 |
| OpenSearch | yes, bcrypt in `internal_users.yml` | the admin only, since the per-user Secrets the chart renders reach no engine | 2, once users reach the engine |
| NATS | yes, bcrypt in the server config | one Secret with every user, plaintext in the child release values | 2 |
| RabbitMQ | yes, `password_hash` through the HTTP API | per-user Secrets and the administrator `-default-user`, and the topology operator imports a password only when it creates the user | 2 |
| Redis, Valkey | yes, `#<sha256>` in ACL | `-auth`, one password shared with replication, the operator and the exporter | 2 |
| MongoDB | no, the server computes SCRAM from the plaintext | `-credentials`, the operator's `databaseAdmin` system user | 3 |
| Bucket | no, SigV4 signs with the secret | per-user keys generated by the COSI driver | 3 |
| VPN (Outline) | no, Shadowsocks derives its key from the password | `ss://` links embedding each password | 3 |
| Qdrant | no, the key is compared as is | the single admin API key, also Prometheus's bearer token | 3 |
| Harbor | no, the admin is bootstrapped from plaintext | the admin and Redis passwords | 3 |
| Monitoring | no, Alerta and a job consume the plaintext | `grafana-admin-password` | 3 |
| Kafka | yes, SCRAM credentials | CA certificates only | none yet: no listener authenticates |
| VM instance | cloud-init takes a crypt hash, but `cloudInit` is free-form text | plaintext in the spec, and the chart's own example is `password: ubuntu` (`packages/apps/vm-instance/values.yaml:111-115`) | out of scope |

Wave 2 engines accept a verifier, but their operator or chart path takes plaintext today, so they convert with the plaintext in the account Secret, like wave 3. Moving each of them to a verifier, the way wave 1 works, is separate work after this proposal, one engine at a time. Wave 3 engines need the plaintext for good. OpenSearch first needs its users to reach the engine, which with plaintext means `OpensearchUser` objects that read the account Secret. Each wave-2 and wave-3 kind gets its own row in §3 at its conversion, shaped by what the table names: a RabbitMQ import that happens only at user creation, keys from the COSI driver, one Qdrant key shared with Prometheus.

Kafka clients connect without credentials, since no listener declares authentication, the optional external LoadBalancer one included (`packages/apps/kafka/templates/kafka.yaml:32-46`). Users with topic ACLs are a Kafka chart feature, and once they exist they join a wave. Until a kind converts, a deployment whose policy requires one-time disclosure can withhold it: `bundles.disabledPackages` stops the platform rendering its package, and on a running cluster the Package also has to be deleted, because packages carry `helm.sh/resource-policy: keep`.

Kubernetes access credentials hold no password and stay at their CA. A managed cluster's `-admin-kubeconfig` is a client certificate and key, its `-oidc-kubeconfig` carries neither, and retiring the static certificate is left open. The Talos workers' `<cluster>-talos-secrets` is not tenant-facing.

## User-facing changes

- The credential view of a converted application lists its accounts with the origin of the live password, the recent operations and the engine state, and offers Regenerate and Revoke to those who may mint. A password appears once, in the dialog that minted it, and creating an application with users, or adding one, chains a mint per new account.
- `use` members stop seeing passwords (§5). Tenants stop seeing MariaDB `root`, the ClickHouse `backup` user and the OpenSearch admin, and administer MariaDB through accounts with the admin role on each database.
- A password an account sets for itself inside PostgreSQL is reverted by CNPG.
- API clients get `credentials` with `mint` and `revoke` in `core.cozystack.io/v1alpha1`, and `tenantsecrets` does not change.
- Operators get a numbered migration per converting kind, an audit policy for distributions that ship none, and a monitoring-agents value for an extra output. The migration's second part is a Job with cluster-admin and `pods/exec` that is not a Helm hook and can be rerun by hand, which the release note says.

## Upgrade and rollback compatibility

A kind converts in one release. The conversion is announced ahead of it, in release notes and on the kind's credential view, so users can store passwords they still need. The API serves, seeds and mints for a kind only once the kind's chart artifact is the converted one (the ApplicationDefinition's `chartRef` keeps its name across versions), so it never overwrites a password the old chart still applies.

The converting release ships the converted chart and a numbered platform migration in two parts. The first is a pre-upgrade hook of the platform release, before any component chart changes version (`packages/core/platform/templates/migration-hook.yaml:27-60`), in every platform variant that installs managed applications. For each release of the kind it writes the account Secrets with the verifiers of the passwords in `<release>-credentials`, origin `Migrated`, so clients keep working. That is additive: nothing an old chart reads changes, so an old chart that renders before the switch breaks nothing.

Then the charts switch, and a surviving `<release>-credentials` is rendered with `internal.cozystack.io/tenantresource: "false"`. For a Secret that exists already this label is the closure, because the webhook stamped it at first admission and skips the object since. For one created while a definition still selects the name, the lineage webhook learns to keep an explicit `"false"` instead of overwriting it, which is why Rollout step 2 ships first. No field ownership moves, since the API takes over no object Helm owns.

The second part is a Job that the platform release creates after the charts switch. It is not a Helm hook, so a long wait does not hold up the upgrade, and it can be run again by hand. For each release of a converting kind it waits until the HelmRelease is Ready on the converted revision, so with the read routes closed, and skips the release after a timeout. Then it removes any leftover `users.*.password` from the values, deletes the tenant keys from `<release>-credentials` (they stay in its `data` otherwise) and the Secret itself where no platform account remains, as in postgres, and rotates the platform passwords of §4. Rotation suspends the release for the change, so no render writes the old value back, and lifts only a suspension it set. ClickHouse also needs a forced upgrade to apply a new `backup` hash, and MariaDB's `root` a connection to the primary, which the Job has and the API lacks (§3). An annotation on `<release>-credentials` records a rotation, so a rerun does not rotate twice, and the rest of the Job is idempotent. A release skipped on the timeout is listed in a ConfigMap in `cozy-system` with the time of the run, and the Job exits non-zero while that list is not empty, so one place shows what is left.

The migration degrades per release, never per fleet. A failing migration stops the whole platform upgrade, so a release it cannot convert (its Secret gone, two users mapping to one name, a user named `app` in postgres) is left out and reported in the migration log and with an Event on its HelmRelease. A left-out release keeps its previous revision, old credentials working and exposed as before, except a PostgreSQL release whose Secret was gone: that takes the converted chart, its database keeps the old passwords, and tenants can no longer read them, so a tenant mints ([the cases](./example.md#when-the-migration-leaves-a-release-out)). Once an operator fixes the cause, the next write through the API starts the conversion: while `<release>-credentials` still holds the plaintext, the API derives the account Secret from it, and otherwise it seeds one and a mint follows; running the Job again finishes the release.

No flag keeps passwords viewable. It would be a second credential path in every converted chart and a switch that turns the guarantee off for every tenant of an installation. An installation that needs the old behaviour for longer stays on the release before the conversion. Pre-conversion revisions keep the old plaintext in Helm history until `MaxHistory` drops them, and the origin stays `Migrated` until the first issuance or revocation makes that value useless.

Downgrading the platform past a conversion is not supported, and it is the one irreversible step: the previous charts generate a new password for every account and apply it over the minted one. A manual `helm rollback` of one release is undone at the next reconcile, because the HelmRelease still asks for the converted chart. Until then MariaDB applies the old passwords from the `<release>-credentials` the old revision recreates, and ClickHouse the verifiers of the older revision, even between two converted ones, so a revoked password logs in again. PostgreSQL keeps the minted passwords, since a rollback runs no post-upgrade hook and CNPG leaves roles dropped from its spec alone, but the old Role exposes the dead plaintext. In every case the answer is a mint once the release is back.

## Security

Each read route, for converted kinds:

| Route | Tenant accounts | Platform accounts |
|---|---|---|
| 1. Application spec | no password field (cozystack/cozystack#4078 for postgres and mariadb, the conversion for the others), and the Job removes leftovers | never in the spec |
| 2. `tenantsecrets` | no definition selects an account Secret | `<release>-credentials` is rendered not tenant-visible |
| 3. Name grants | none | none, which closes cozystack/cozystack#4164 for converted kinds |
| 4. Helm history | no tenant password in a converted revision, and older revisions keep the pre-conversion value until `MaxHistory` drops them (hence the `Migrated` origin) | open by decision: chart-rendered plaintext, readable only by management-cluster administrators and platform components |
| 5. Operator-materialised configuration | verifiers only | unchanged |

The mint response is the one place the plaintext exists by design. It crosses from kube-apiserver to the Cozystack API over TLS and back, and neither server logs or audits it (§6). In waves 2 and 3 the account Secret holds the plaintext as well, which tenants cannot read and platform components can.

The trust boundary does not move. The Cozystack API already holds `create`, `update`, `patch` and `delete` on Secrets in every namespace (`packages/system/cozystack-api/templates/rbac.yaml:18-20`), and Flux renders every tenant's password today, so generation in the API adds no authority over credentials. The API gains `create` on `events` and read access to the engine objects it reports on: CNPG `clusters`, mariadb-operator `users` and ClickHouse installations. The post-upgrade Job needs the rights the migration hook holds today (cluster-admin for the length of the run, `migration-hook.yaml:62-77`) and reaches each MariaDB primary through `pods/exec`, and its database access is no more than mariadb-operator's root login.

The lineage webhook's `tenantresource` verdict is not a control. A chart may narrow it, since an explicit `"false"` is kept, and a chart-set `"true"` is still overwritten whenever the webhook runs. But the webhook is not called for an object that already carries `internal.cozystack.io/managed-by-cozystack` (`mutatingwebhookconfiguration.yaml:43-46`), so a writer that sets that label gets the `tenantresource` it wrote. A Secret stays off `tenantsecrets` because the converted chart renders `"false"` on those that exist, no definition selects later ones, the webhook keeps the chart's `"false"` in between, and no converted chart labels any `"true"`.

Verifiers are shown to no one, although they are not treated as secrets. A verifier of a 32-character random password leaves no practical offline attack, which is the only reason ClickHouse's unsalted SHA-256 and MariaDB's double SHA-1 are good enough, and a migrated password keeps the `Migrated` origin anyway. mariadb-operator pastes the hash into SQL unescaped, which is safe because only the platform writes the account Secret. For ClickHouse the verifier arrives under a `_`-prefixed values key, which the Application API hides on read and refuses on write (`pkg/registry/apps/application/rest.go:1269-1304`).

The mint body carries a precondition and no other tenant input. A Credential lives in its tenant namespace, kube-apiserver authorizes a mint by RBAC in that namespace, and the API resolves the account Secret within it. Without OIDC the tiers collapse into the tenant ServiceAccount (§5).

## Failure and edge cases

- The engine's operator is down, or the database is not ready → the mint succeeds and `status.engine` stays Pending (Unknown for MariaDB), with the old password working until the engine applies the new one.
- A ClickHouse release is suspended or failing → the mint is accepted and stays Pending until the release upgrades.
- A user leaves `users` while a mint for it is in flight → NotFound or Conflict, and no password.
- An account Secret deleted by hand → the engine keeps the account as it is (§3) and a ClickHouse release keeps its previous revision, until a mint recreates the Secret with a new password and identity. A ClickHouse user added or renamed around the API → the values check fails the render until the next write through the API rewrites the list. The account shows Failed.
- A create that fails after the Secrets were written, or a delete with orphan propagation → Secrets no current HelmRelease owns, which the next create under that name replaces instead of reusing.
- Deleting a converted application whose release is suspended → refused, because Flux skips the uninstall of a suspended release and would leave the database running with its last passwords after the account Secrets are gone. This is new Application API behaviour and goes in the release note.
- A PostgreSQL name that makes CNPG's webhook reject the whole Cluster → refused at render, as `postgres` is today. That covers roles CNPG reserves (`streaming_replica`, anything starting with `pg_` or `cnpg_`), a database whose derived `<db>_admin` role would be reserved, an account named like a derived role, and an account named `app`, the owner CNPG creates at initdb and whose password its instance manager resets from `<release>-app` on every start.
- A restore in place → account Secrets and records stay, and the operators apply the current values over the restored catalog. For PostgreSQL CNPG does it once the restored primary is up, replacing the init-job run and the `credentials-pending` annotation of today's restore path.
- A restore into a new application → new records and seeds, and the restored accounts need a mint, as they need new passwords today.
- An application is deleted → its account Secrets go with its HelmRelease, and the audit trail remains.
- The hook or the Job is interrupted → both are idempotent, rotation is skipped where the annotation says it is done, and the next run finishes what is left.
- A `password` left in the values of a converted kind → dropped with an admission warning pointing at mint, extending the warning cozystack/cozystack#4078 added for postgres and mariadb.

## Testing

- API unit tests: passwords are 32 characters from the stated alphabet and `crypto/rand`, every verifier checks out against a reference implementation (PostgreSQL's SCRAM, MariaDB's `PASSWORD()`, SHA-256) with the SCRAM form canonical, conflicts return no password, and a captured log and event recorder never see the plaintext. The API-change gate learns the subresource keys its storage-key pattern skips today.
- Chart unit tests on each converted chart: no tenant password in any rendered object, the engine points at the account Secret, neither account Secrets nor `<release>-credentials` are granted or selected, `<release>-credentials` renders `tenantresource: "false"` and no rendered Secret carries `"true"`, the ClickHouse render fails for a declared account with no verifier in its values, and reserved or colliding names are refused. API and reconciler build the same `valuesFrom` list.
- Writes through `tenantsecrets`, whose code has no tests today: no tenant tier holds a write verb on it, and an update or delete through it on an account Secret or on `<release>-credentials` answers NotFound.
- Lineage webhook: an explicit `"false"` is kept where a definition selects the Secret, and a chart-set `"true"` is overwritten.
- No foreign secret, end to end with a parent and two sibling tenants: `tenantsecrets` never lists an account Secret or `<release>-credentials`, a sibling can neither read nor mint a tenant's credentials, and a parent's mint shows in the child's record.
- No second disclosure, end to end for each wave-1 engine: mint, log in, and find the password on no read path (the Credential, `tenantsecrets`, a direct `get` on the Secret, the application, Events, the audit log, the Helm history of the converted revision). Then mint again and see the first password fail, once `status.engine` says Applied for PostgreSQL and ClickHouse, and by polling a login for MariaDB.
- Live checks on the pinned versions ran on plain operator objects: CNPG 1.30.0, mariadb-operator 25.10.2, clickhouse-operator 0.25.2 and helm-controller 1.5.0 behave as §3 and §4 state when an account Secret is missing or changes, and kube-apiserver 1.34 and 1.37 record no aggregated call body.
- The hook and the Job: clients keep logging in at every point, including while an old chart renders between the hook and the switch, accounts show origin `Migrated`, no `users.*.password` remains, a Job stopped between `ALTER USER` and the Secret write recovers on retry, rotated platform passwords work, and no tenant route reads a converted release's `<release>-credentials`. A release it leaves out does what its cause says in [`example.md`](./example.md#when-the-migration-leaves-a-release-out).
- Audit: a mint appears in the kube-apiserver log with `objectRef.subresource: mint` and the Credential's name.

## Rollout

1. This proposal is accepted.
2. Independent change: the lineage webhook keeps an explicit `tenantresource: "false"` and still overwrites a chart-set `"true"`.
3. The Cozystack API gains `credentials`, `mint`, `revoke`, seeding and the engine wiring of §3 for PostgreSQL, MariaDB and ClickHouse, serving no kind yet, with the Event and its RBAC. The documentation gets the audit policies, and monitoring-agents the output value.
4. Wave 1: PostgreSQL and MariaDB convert once the checks left under Testing pass, and ClickHouse with them or a release later.
5. Wave 2: OpenSearch, NATS, RabbitMQ, Redis and Valkey, each with its §3 row and the plaintext in its account Secret.
6. Wave 3: MongoDB, Bucket, VPN, Qdrant, Harbor and Monitoring, the same way. Kafka joins whichever wave is open once its chart has users.
7. After this proposal, as separate tasks: each wave-2 engine moves from stored plaintext to a verifier.

## Open questions

- Whether MariaDB gets a real engine state by reading the stored hash back through a privileged connection (§3), or stays `Unknown`.

## Alternatives considered

- Keeping the plaintext and guarding a one-time collection window over it, with a durable journal of every access (the earlier revision of this proposal). All of it exists to guard stored plaintext, which verifiers remove.
- Keeping generation in the chart, as today. The plaintext stays in Helm history for `MaxHistory` revisions, so a rotation cannot revoke and a `helm rollback` restores the old value. It stays only for platform accounts, whose history no tenant can read.
- Moving every platform account into a new Secret. MariaDB's `root` reference cannot change once set, so one engine would stay behind in any case.
- A stored `Credential` kind next to the account Secret. CNPG needs a Secret per role anyway, and a record on it cannot drift from the value it describes.
- A subresource on the application, such as `postgreses/<name>/mint` with the user in the body. The audit event would name the application and not the account, and RBAC could not address one account.
- Writing the verifier into the live `Cluster` or CHI. Postgres releases are applied client-side, so the patch vanishes at the next render, and a ClickHouse render that lands first gives the account the default password.
- Postgres through the init-job with a forced upgrade per call, which runs the whole chart every time and does nothing on a suspended release.
- ClickHouse through `valueFrom.secretKeyRef`, which restarts the pods on every change, or `k8s_secret_password_sha256_hex`, which operator 0.27.4 removes.

---

<!--
Inspired by KubeVirt enhancement proposals
(https://github.com/kubevirt/enhancements) and Kubernetes Enhancement
Proposals (KEPs).
-->
