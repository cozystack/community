# Walkthrough: one PostgreSQL application

This file follows one application through the design in [README.md](./README.md), step by step, with the objects each step creates or changes. The README says why things work this way, and this file shows what happens. The annotation keys are illustrative.

The tenant is `tenant-acme`, the application is a `Postgres` named `orders`, so the release is `postgres-orders`, and it has one user, `web`, with the admin role on the database `shop`. How MariaDB and ClickHouse differ is at the end.

## 1. A tenant administrator creates the application

The request reaches the Cozystack API from the dashboard, `kubectl` or any other client. No field carries a password.

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: Postgres
metadata:
  name: orders
  namespace: tenant-acme
spec:
  users:
    web: {}
  databases:
    shop:
      roles:
        admin: [web]
```

## 2. The API seeds the account Secret

Before it writes the HelmRelease, the API writes one Secret per user. The password under `password` is the SCRAM verifier of a random value that the API threw away, so the account can exist in the database while nobody can log in as it.

```yaml
apiVersion: v1
kind: Secret
type: kubernetes.io/basic-auth
metadata:
  name: postgres-orders.web.account
  namespace: tenant-acme
  labels:
    cnpg.io/reload: "true"                    # CNPG watches the Secret
  annotations:
    credentials.cozystack.io/origin: NotIssued
    credentials.cozystack.io/history: '[{"operation":"Create","at":"2026-10-02T09:10:02Z","by":"jane@example.org"}]'
data:
  username: web
  password: SCRAM-SHA-256$4096:...            # base64 in the real object
```

Then the API writes the HelmRelease `postgres-orders` and adds it to the Secret as owner. No ApplicationDefinition selects the Secret and no Role grants it, so no tenant can read it.

## 3. The chart renders the roles

The chart puts the database roles and the user under CNPG's declarative role management and points the user at its account Secret.

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgres-orders
  namespace: tenant-acme
spec:
  managed:
    roles:
    - name: shop_admin
      login: false
      inherit: false
      comment: role managed by helm
    - name: shop_readonly
      login: false
      inherit: false
      comment: role managed by helm
    - name: web
      login: true
      inherit: true
      replication: false
      comment: user managed by helm
      inRoles: [shop_admin]
      passwordSecret:
        name: postgres-orders.web.account
```

The init-job creates the database `shop`, hands it to `shop_admin`, sets object privileges and extensions, and sets no password and no membership.

## 4. CNPG creates the role

The instance manager on the primary creates `web` with the seeded verifier and reports the version of the Secret it applied.

```yaml
status:
  managedRolesStatus:
    passwordStatus:
      web:
        resourceVersion: "48190"              # the account Secret's resourceVersion
        transactionID: 812
```

## 5. The tenant sees the account, not a password

Any tenant member at `view` or above can list the accounts.

```
$ kubectl get credentials -n tenant-acme
NAME                  APPLICATION       USER   ORIGIN
postgres-orders.web   Postgres/orders   web    NotIssued
```

## 6. The administrator mints a password

The dashboard mints right after step 1, so the person who created the application sees the password once, in the creation dialog. Later mints come from the Regenerate button, `kubectl create --raw` or any client.

```
POST /apis/core.cozystack.io/v1alpha1/namespaces/tenant-acme/credentials/postgres-orders.web/mint
{"apiVersion": "core.cozystack.io/v1alpha1", "kind": "CredentialRequest", "spec": {"preconditions": {"resourceVersion": "48190"}}}

201 Created
{"apiVersion": "core.cozystack.io/v1alpha1", "kind": "CredentialRequest", "status": {"username": "web", "password": "<32 characters, shown once>", "issuedAt": "2026-10-02T09:14:07Z"}}
```

Inside the mint the API generated the 32 characters, computed their verifier, wrote it into `postgres-orders.web.account` in one update conditioned on `resourceVersion: "48190"`, and put the plaintext into the response. The Secret now reads:

```yaml
metadata:
  name: postgres-orders.web.account
  resourceVersion: "48240"
  annotations:
    credentials.cozystack.io/origin: Minted
    credentials.cozystack.io/history: '[{"operation":"Mint","at":"2026-10-02T09:14:07Z","by":"jane@example.org"},{"operation":"Create","at":"2026-10-02T09:10:02Z","by":"jane@example.org"}]'
data:
  username: web
  password: SCRAM-SHA-256$4096:...            # the verifier of the new password
```

The plaintext is in no object. If the response is lost, nobody holds this password, and the next call replaces it.

## 7. CNPG applies the new verifier

The reload label makes CNPG notice the change. The instance manager runs `ALTER ROLE web PASSWORD 'SCRAM-SHA-256$...'`, and PostgreSQL stores the verifier as it is. CNPG reports `resourceVersion: "48240"` for `web`, and the new password works from then on. The `Credential` shows the record only, and says nothing about the engine:

```yaml
apiVersion: core.cozystack.io/v1alpha1
kind: Credential
metadata:
  name: postgres-orders.web
  namespace: tenant-acme
  resourceVersion: "48240"
spec:
  application: {apiGroup: apps.cozystack.io, kind: Postgres, name: orders}
  user: web
status:
  password: {origin: Minted, at: "2026-10-02T09:14:07Z", by: jane@example.org}
  history:
  - {operation: Mint, at: "2026-10-02T09:14:07Z", by: jane@example.org}
  - {operation: Create, at: "2026-10-02T09:10:02Z", by: jane@example.org}
```

## 8. What the audit log and the events show

kube-apiserver records the call before it proxies it to the Cozystack API, at `Metadata` level and without a body:

```json
{"kind": "Event", "apiVersion": "audit.k8s.io/v1", "level": "Metadata", "stage": "ResponseComplete",
 "verb": "create",
 "requestURI": "/apis/core.cozystack.io/v1alpha1/namespaces/tenant-acme/credentials/postgres-orders.web/mint",
 "user": {"username": "jane@example.org", "groups": ["tenant-acme-admin", "system:authenticated"]},
 "sourceIPs": ["10.244.1.17"],
 "objectRef": {"resource": "credentials", "namespace": "tenant-acme", "name": "postgres-orders.web",
               "apiGroup": "core.cozystack.io", "apiVersion": "v1alpha1", "subresource": "mint"},
 "responseStatus": {"code": 201},
 "annotations": {"authorization.k8s.io/decision": "allow"}}
```

monitoring-agents ships this line to VictoriaLogs, and a SIEM can take it from there and alert on `objectRef.subresource: mint`. The application also gets an Event, which reaches VictoriaLogs the same way:

```
$ kubectl get events -n tenant-acme --field-selector involvedObject.name=orders
TYPE     REASON             MESSAGE
Normal   CredentialIssued   password of user web issued by jane@example.org
```

## 9. A second administrator acts at the same time

Bob opened the dashboard before step 6, so his Regenerate sends the version he saw:

```
POST .../credentials/postgres-orders.web/mint
{"kind": "CredentialRequest", "spec": {"preconditions": {"resourceVersion": "48190"}}}

409 Conflict
{"kind": "Status", "reason": "Conflict",
 "message": "credential postgres-orders.web changed: password issued by jane@example.org at 2026-10-02T09:14:07Z"}
```

Bob gets no password, and Jane's keeps working. If Bob regenerates again, knowing this, his mint replaces Jane's password and is recorded under his name.

## 10. The password leaks: revocation

```
POST /apis/core.cozystack.io/v1alpha1/namespaces/tenant-acme/credentials/postgres-orders.web/revoke
{"apiVersion": "core.cozystack.io/v1alpha1", "kind": "CredentialRequest", "spec": {"preconditions": {"resourceVersion": "48240"}}}

201 Created
```

The response carries no password. The API writes into the Secret the verifier of a new random password and throws that password away. CNPG applies it, and `web` can no longer log in with any password anyone holds. The origin becomes `Revoked`, the audit log shows a `create` on the `revoke` subresource, and the Event reads `CredentialRevoked`. Connections that were already open stay open. Getting a working password back is step 6 again.

## 11. The tenant removes the user

The tenant updates the application with `users: {}`. The API writes the new HelmRelease, and the chart drops `web` from `managed.roles`, so CNPG stops managing it. The init-job drops the role `web`, because it carries the comment `user managed by helm` and is no longer declared, after reassigning what it owns. Once the HelmRelease write has landed, the API deletes `postgres-orders.web.account`, and `kubectl get credentials` no longer lists the account.

## 12. The tenant deletes the application

Helm uninstalls the release, and Kubernetes garbage-collects the account Secrets together with the HelmRelease that owns them. The history of `web` survives in the audit log only. A new `orders` created later under the same name gets new Secrets with new UIDs, and nothing from the deleted one carries over.

## Converting an application that already exists

Before the conversion, the chart keeps `web`'s password in plaintext in `postgres-orders-credentials`, readable through `tenantsecrets`, the name grant and Helm history:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: postgres-orders-credentials
  namespace: tenant-acme
stringData:
  web: "<16 characters>"
```

The release that converts PostgreSQL runs a hook before any chart changes. It records the chart version of the release and writes `postgres-orders.web.account` with the verifier of that same password, origin `Migrated`, and touches nothing else. Then the new chart renders the `managed.roles` of step 3. CNPG takes over `web` and applies the verifier of the same password, so every client keeps working. Once the release is Ready on a chart version other than the recorded one, the Job removes a `users.web.password` if one was left in the HelmRelease values and deletes `postgres-orders-credentials`, since PostgreSQL keeps no platform account there. The record shows `Migrated` until someone mints or revokes, and older Helm revisions keep the old plaintext until `MaxHistory` drops them.

## When the migration leaves a release out

A release the migration cannot convert is reported in the migration log and with an Event on its HelmRelease, and the cause decides what it does once the converted charts arrive.

| Cause | What happens |
|---|---|
| Colliding users, or a postgres user named `app` | The converted chart refuses to render. The release keeps its previous revision, with old credentials working and exposed as before. |
| A MariaDB release without `<release>-credentials` | The converted chart keeps the root guard of today's chart and fails the upgrade, so the release keeps its previous revision. |
| ClickHouse account Secrets that do not exist | The HelmRelease names them in `valuesFrom`, Flux stops the release with `ValuesError`, and it keeps its previous revision. |
| A PostgreSQL release whose Secret was gone | The converted chart renders. CNPG leaves a role without an account Secret untouched, so the old passwords keep working, and tenants can no longer read them. A tenant mints a new one. |

Once an operator fixes the cause, running the Job again writes the account Secrets that are missing, from the plaintext in `<release>-credentials` while it is there, lets the release switch and finishes it. An account with no Secret and no plaintext is listed with no origin, and a mint creates the Secret.

## How MariaDB and ClickHouse differ

| Step | MariaDB | ClickHouse |
|---|---|---|
| 2, the account Secret | labelled `k8s.mariadb.com/watch`, `password` holds `PASSWORD()` output (`*` and 40 hex digits) | labelled `reconcile.fluxcd.io/watch: Enabled`, `password` holds the SHA-256 hex, and the HelmRelease gets a `valuesFrom` entry for the Secret, keyed by a digest of the user name |
| 3, the chart | the `User` points `passwordHashSecretKeyRef` at the account Secret | the chart renders `password_sha256_hex` into the CHI from the value that entry supplies |
| 7, applying a new value | the operator re-runs `ALTER USER` | helm-controller upgrades the release, the operator rewrites `chi-<chi>-common-usersd`, and ClickHouse re-reads it within seconds |
| Conversion | `<release>-credentials` keeps `root`, which the Job rotates with `ALTER USER` | `<release>-credentials` keeps `backup`, which the Job rotates, restarting the ClickHouse pods once |

## How the kinds declare their accounts

The block is `spec.credentials` of the ApplicationDefinition of the kind, so a tenant administrator, who can edit the application, cannot change how its passwords are stored.

| Kind | Block |
|---|---|
| PostgreSQL | `format: scram-sha-256`, `secretLabels: {cnpg.io/reload: "true"}`, `users: users` |
| MariaDB | `format: mysql-native`, `secretLabels: {k8s.mariadb.com/watch: ""}`, `users: users`, `accounts: [{name: root, owner: platform}]` |
| ClickHouse | `format: sha256`, `secretLabels: {reconcile.fluxcd.io/watch: Enabled}`, `users: users`, `valuesPath: _accounts`, `accounts: [{name: backup, owner: platform}]` |
| Redis | `format: plaintext`, `accounts: [{name: default, when: authEnabled}]` |

`owner` is `tenant` unless the entry says otherwise. MariaDB's `root` and ClickHouse's `backup` are `owner: platform`: they stay in `<release>-credentials`, and the API does nothing for them, so there is no `Credential` and no account Secret for either, and a mint cannot reach them. The entry also keeps a `users.root`, which the MariaDB chart ignores today, from being listed, seeded or migrated as a tenant account. ClickHouse's chart refuses its reserved names itself.

## A kind with no users: Redis

Redis has no `users`: one password, in `redis-cache-auth` for an application `cache`, which the chart generates with `lookup` and the operator reads through `auth.secretPath`. Its ApplicationDefinition declares that password as a fixed account, present while `authEnabled` is true:

```yaml
spec:
  credentials:
    format: plaintext      # the account Secret holds the password itself, as in wave 2
    accounts:
    - name: default        # Credential redis-cache.default
      when: authEnabled    # the values key that switches the account on, true by default
```

The chart stops generating the password and points the operator at the account Secret that the API seeds, whose `password` key has the name the operator reads today:

```yaml
  {{- if .Values.authEnabled }}
  auth:
    secretPath: {{ .Release.Name }}.default.account
  {{- end }}
```

The account starts as `NotIssued`, and a mint replaces the password in that Secret. How a changed password reaches the replicas and the sentinels is the Redis row of §3 at its conversion.

## Headless provisioning and Terraform

A pipeline with no human in it creates the application, and the accounts start as `NotIssued`. It mints from the tenant ServiceAccount, which holds the verb already, and stores the response where the workload reads it, a Secret of the tenant or its own password store. Storing is the caller's, so the platform guarantees that it holds no plaintext, not that the tenant keeps none. A GitOps helper or operator that mints and writes the result into the tenant's external password store can be built on this call, and is later work.

A Terraform resource that wraps mint keeps the plaintext in state, as `aws_iam_access_key` does. That is the caller's choice and not for the strictest policies. `import` returns the metadata and no password, and a mint made elsewhere shows in `status.password.at` and `by`, which the provider reads as drift.
