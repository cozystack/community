# The contract for a kind, and a walkthrough

This file has two parts. [The contract for a kind](#the-contract-for-a-kind) shows what the author of a kind writes in its definition, what its chart renders and what the API writes, for PostgreSQL, for ClickHouse, whose operator reads no Secret, and for a kind outside the tree that is not a database. The [walkthrough](#walkthrough-one-postgresql-application) follows one PostgreSQL application through the design in [README.md](./README.md), step by step, with the objects each step creates or changes. The README says why things work this way, and this file shows what happens.

## The contract for a kind

Three parties each have a fixed part. The author of a kind writes the `credentials` block in its ApplicationDefinition. The chart renders what the engine or the pod needs and nothing secret. The Cozystack API writes the account Secrets and serves the mint, and knows nothing of the kind beyond its block.

Three kinds follow, and together they show every part of the block:

| Part of the block | Shown in |
|---|---|
| `ValuesMap` | all three |
| `format` with a built-in function | PostgreSQL (`scram-sha-256`), ClickHouse (`sha256`) |
| `format` the API does not have yet | nginx (`apr1`) |
| `secretLabels` | PostgreSQL, ClickHouse |
| `Static` with `owner: platform`, and the platform's own Secret | ClickHouse |
| `valuesPath` and `valuesFrom` | ClickHouse |
| `Static` with `when`, and `format: plaintext` | [Redis](#a-kind-with-no-users-redis), below |
| the engine reads the account Secret | PostgreSQL |
| the pod reads the account Secret | nginx |
| the chart gets the verifier as a value | ClickHouse |

### PostgreSQL

What the author writes in the definition. The block sits next to the `release` and `secrets` fields it interacts with, and each field carries a line saying why it is there.

```yaml
apiVersion: cozystack.io/v1alpha1
kind: ApplicationDefinition
metadata: {name: postgres}
spec:
  application:
    kind: Postgres
    openAPISchema: |-            # users is a map of flags with no password property; the API drops one with a warning
      {"type":"object","properties":{"users":{"type":"object","additionalProperties":{"type":"object","properties":{"replication":{"type":"boolean"},"createRole":{"type":"boolean"},"bypassRls":{"type":"boolean"}}}}}}
  release:
    prefix: postgres-            # the release is postgres-<name>, and the account Secrets are named after it
    chartRef: {kind: ExternalArtifact, name: cozystack-postgres-application-default-postgres, namespace: cozy-system}
  credentials:
    format: {name: scram-sha-256}              # the account Secret holds a SCRAM verifier, which CNPG stores as it is
    secretLabels: {cnpg.io/reload: "true"}     # CNPG notices a changed Secret only with this label
    accounts:
    - {kind: ValuesMap, path: users}           # the keys of spec.users are the tenant accounts
                                               # no Static entry: PostgreSQL has no fixed or platform account
  secrets:
    include:
    - matchLabels: {internal.cozystack.io/tenant-ca: "true"}   # the public trust anchor; the entry for <release>-credentials is gone
```

What the chart renders, and what it must not. No `lookup`, no `randAlphaNum`, no Secret or init-script holding a password, and no Role or `secrets` entry that names an account Secret. PostgreSQL has no platform account, so after the migration there is no `<release>-credentials` at all. A kind that has one renders that Secret with `internal.cozystack.io/tenantresource: "false"` (README §4). The chart cannot read an account Secret at render time, so it only names it, and CNPG reads it at runtime through `passwordSecret`, which the reload label from the block keeps current. The name is the API's mapping, which the chart computes with a helper, and a chart test compares the helper with the API's function, because CNPG skips a role whose Secret is not there, with no error. [Step 3](#3-the-chart-renders-the-roles) has the whole rendered `managed.roles`, and this is the part that names the Secret:

```yaml
{{- range $user, $_ := .Values.users }}
- name: {{ $user }}
  login: true
  passwordSecret:
    name: {{ $.Release.Name }}.{{ include "postgres.accountName" $user }}.account   # the API's name mapping
{{- end }}
```

What the API writes. On create, before the HelmRelease, one Secret per key of `spec.users`. On an update, one more for each key the write adds and a delete for each key it removes. The Secret for `web` is the one in [step 2](#2-the-api-seeds-the-account-secret), and every field of it is fixed:

- `type: kubernetes.io/basic-auth`
- the name `postgres-orders.web.account`, from the release and the user name lowercased with every character outside `[a-z0-9-]` replaced by `-`, and two names that map to the same object name, or to an invalid one, are refused
- the labels `apps.cozystack.io/application.kind`, `.group` and `.name` of the application, which the view lists by, and `cnpg.io/reload: "true"` from `secretLabels`
- the annotations `credentials.cozystack.io/origin` (`NotIssued`, `Migrated`, `Minted` or `Revoked`) and `credentials.cozystack.io/history` (a JSON list of the last ten `{operation, at, by}`, newest first)
- the data keys `username`, the original name, and `password`, the SCRAM verifier of a random value that the API discarded
- the HelmRelease as owner

A mint replaces `password` and the two annotations in one update that fails if the Secret changed since the caller read it, and returns the plaintext once ([step 6](#6-the-administrator-mints-a-password)).

### A kind outside the tree: nginx `auth_basic`

`MyApp` is a web application behind nginx `auth_basic`, and its kind lives outside the repository. It is not a database, so no operator reads the account Secret and the pod does. `apr1`, the htpasswd hash, is not one of the four formats of README §3. This section takes it as a fifth function in `pkg/credentials/format`, a change to the repository. A kind that needs a format the API lacks names `plaintext` and hashes in the pod, or waits for the format, and a webhook format (README §7) would let it bring its own.

What the author writes in the definition.

```yaml
apiVersion: cozystack.io/v1alpha1
kind: ApplicationDefinition
metadata: {name: myapp}
spec:
  application:
    kind: MyApp
    openAPISchema: |-            # users is a map with no password property; the API drops one with a warning
      {"type":"object","properties":{"users":{"type":"object","additionalProperties":{"type":"object"}}}}
  release: {prefix: myapp-, chartRef: {kind: ExternalArtifact, name: myapp-chart, namespace: cozy-system}}
  credentials:
    format: {name: apr1}                       # the account Secret holds an htpasswd hash
    accounts:
    - {kind: ValuesMap, path: users}           # the keys of spec.users are the tenant accounts
                                               # no secretLabels: nothing but the pod reads the Secrets
                                               # no Static entry: no fixed or platform account in this kind
  secrets: {include: []}                       # nothing credential-bearing is tenant-visible
```

What the chart renders, and what it must not. The same rules: no `lookup`, no `randAlphaNum`, no Secret with a password in it, and nothing in the dashboard resource map that grants a Secret. The chart cannot read the account Secrets at render time, so the pod reads them at runtime: one Secret volume per declared account, and a sidecar that composes the file nginx reads. `optional: true` also hides a wrong name. If the helper maps a user differently from the API, the volume stays empty and nobody can log in, with no error, which is why the chart test that compares the helper with the API's mapping applies here as well.

```yaml
volumes:
  {{- range $user, $_ := .Values.users }}
  - name: account-{{ include "myapp.accountName" $user }}       # the API's name mapping
    secret:
      secretName: {{ $.Release.Name }}.{{ include "myapp.accountName" $user }}.account
      optional: true            # a Secret written around the API must not block the pod
  {{- end }}
  - {name: auth, emptyDir: {}}
containers:
  - name: nginx                 # auth_basic_user_file /etc/nginx/auth/htpasswd, re-read per request
    volumeMounts: [{name: auth, mountPath: /etc/nginx/auth, readOnly: true}]
  - name: htpasswd-sync         # writes "<username>:<password>" from the two keys of each mounted account, renames over htpasswd, loops on change
```

What the API writes. On create, before the HelmRelease, one Secret per key of `spec.users`. On an update that adds a key, one more, and on one that removes a key, a delete. For `users: {alice: {}, Bob.Smith: {}}` the second is:

```yaml
apiVersion: v1
kind: Secret
type: kubernetes.io/basic-auth
metadata:
  name: myapp-site.bob-smith.account            # lowercased, characters outside [a-z0-9-] replaced by -
  namespace: tenant-acme
  labels:
    apps.cozystack.io/application.group: apps.cozystack.io
    apps.cozystack.io/application.kind: MyApp
    apps.cozystack.io/application.name: site    # the view lists the account Secrets by these
  ownerReferences: [{kind: HelmRelease, name: myapp-site}]   # apiVersion and uid omitted; set right after the HelmRelease write on create, from the start on an update
  annotations:
    credentials.cozystack.io/origin: NotIssued
    credentials.cozystack.io/history: '[{"operation":"Create","at":"...","by":"jane@example.org"}]'
data:
  username: Bob.Smith                           # the original name
  password: $apr1$...                           # hash of a random value the API discarded
```

Nobody can log in until a mint. `POST .../credentials/myapp-site.bob-smith/mint` replaces `password` and the annotations in one conditional write and returns the plaintext once. The mounted file changes on the kubelet's sync period, the sidecar rewrites `htpasswd`, and the new password works. `kubectl get credentials -n tenant-acme` lists both accounts with their origin, `-o yaml` adds the history, and neither shows a password.

### ClickHouse: an operator that reads no Secret, and a platform account

ClickHouse differs from the two kinds above in two ways. Its operator watches no Secrets, and the one live path to a user's password is a hash in the `ClickHouseInstallation`, which Helm renders, so the chart gets the verifier as a value and the HelmRelease pulls it from the account Secret. And it has a platform account, `backup`, which the backup jobs use and no tenant may see.

What the author writes in the definition.

```yaml
apiVersion: cozystack.io/v1alpha1
kind: ApplicationDefinition
metadata: {name: clickhouse}
spec:
  application:
    kind: ClickHouse
    openAPISchema: |-            # users is a map with a readonly flag and no password property; the API drops one with a warning
      {"type":"object","properties":{"users":{"type":"object","additionalProperties":{"type":"object","properties":{"readonly":{"type":"boolean"}}}}}}
  release:
    prefix: clickhouse-
    chartRef: {kind: ExternalArtifact, name: cozystack-clickhouse-application-default-clickhouse, namespace: cozy-system}
  credentials:
    format: {name: sha256}                              # ClickHouse stores an unsalted SHA-256 hex, which is the value in the account Secret
    secretLabels: {reconcile.fluxcd.io/watch: Enabled}  # Flux reconciles the HelmRelease when an account Secret changes
    valuesPath: _accounts                               # the operator cannot read Secrets, so the verifiers reach the chart as values under this key
    accounts:
    - {kind: ValuesMap, path: users}                    # the keys of spec.users are the tenant accounts
    - {kind: Static, name: backup, owner: platform}     # the platform's account: no Credential, no account Secret, no mint, and a users.backup is skipped
  secrets: {include: []}                                # the entry for <release>-credentials is gone, since that Secret now holds only backup
```

What the chart renders, and what it must not. For tenant accounts the same rules as before: no `lookup`, no `randAlphaNum`, no password in any rendered object, and no Role or `secrets` entry that names an account Secret. The chart takes the verifier of each declared user from `.Values._accounts`, under the key `u<digest>`, where `<digest>` is the SHA-256 of the user name as `users` lists it. A user name fits neither a `targetPath` nor a value, since a comma fails the entry, while a hex digest fits both and leaves no order to keep aligned. The render fails when a declared account has no verifier, as a HelmRelease written around the API leaves it, because the operator would give such an account the password `default`. It also refuses tenant accounts named `backup`, `default` or `clickhouse_operator`. `backup` stays as it is today: the chart generates it with `lookup`, keeps it in `<release>-credentials`, which it renders with `internal.cozystack.io/tenantresource: "false"`, and the backup CronJob and the sidecar read it from there.

```yaml
configuration:
  users:
    {{- range $name, $u := .Values.users }}
    {{- $verifier := get ($.Values._accounts | default dict) (printf "u%s" (sha256sum $name)) | required (printf "no verifier for account %s" $name) }}
    {{ $name }}/password_sha256_hex: {{ $verifier }}
    {{ $name }}/profile: {{ ternary "readonly" "default" ($u.readonly | default false) }}
    {{ $name }}/networks/ip: ["::/0"]
    {{- end }}
    backup/password_sha256_hex: {{ sha256sum $backupPassword }}   # from the lookup of <release>-credentials, as today
```

What the API writes. For a release `clickhouse-reports` with `users: {analytics: {readonly: true}}` it writes the account Secret, with the same fields as in the PostgreSQL case, the Flux label in place of the CNPG one, and a SHA-256 hex in `password`:

```yaml
apiVersion: v1
kind: Secret
type: kubernetes.io/basic-auth
metadata:
  name: clickhouse-reports.analytics.account
  namespace: tenant-acme
  labels:
    apps.cozystack.io/application.group: apps.cozystack.io
    apps.cozystack.io/application.kind: ClickHouse
    apps.cozystack.io/application.name: reports
    reconcile.fluxcd.io/watch: Enabled          # from secretLabels
  ownerReferences: [{kind: HelmRelease, name: clickhouse-reports}]   # apiVersion and uid omitted
  annotations:
    credentials.cozystack.io/origin: NotIssued
    credentials.cozystack.io/history: '[{"operation":"Create","at":"...","by":"jane@example.org"}]'
data:
  username: analytics
  password: <64 hex digits>                     # SHA-256 of a random value the API discarded
```

It also writes one `valuesFrom` entry per account into the HelmRelease. The API and the ApplicationDefinition reconciler build that list with the same function, and the reconciler writes it onto the releases that exist. The digest below is the SHA-256 of `analytics`, and `backup` gets no entry, no Secret and no Credential.

```yaml
spec:
  valuesFrom:
  - {kind: Secret, name: cozystack-values}      # as today
  - kind: Secret
    name: clickhouse-reports.analytics.account
    valuesKey: password
    targetPath: _accounts.u65f98121a162a56ad8ee919ed9ea394b9eabd8714616acf30636c6092ee350b9
```

A mint changes `password` in that Secret. The label makes Flux reconcile the HelmRelease, helm-controller reads `valuesFrom` again and upgrades the release with the new hash, the operator rewrites the `chi-<chi>-common-usersd` ConfigMap, and ClickHouse re-reads it within seconds. So a mint costs a release upgrade, and a suspended or failing release holds it until the release recovers. `kubectl get credentials` lists `analytics` and never `backup`. The migration rotates `backup` once: the Job writes a new value into `<release>-credentials` and forces an upgrade, which applies its hash and restarts the ClickHouse pods once for the sidecar (README §4).

That is the whole contract for a kind. PostgreSQL's page differs from nginx's where CNPG reads `passwordSecret` and the reload label takes the place of the volume and the sidecar, and ClickHouse's where the verifier travels through `valuesFrom` into the chart.

## Walkthrough: one PostgreSQL application

This part follows one application through the design, step by step, with the objects each step creates or changes.

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
    apps.cozystack.io/application.group: apps.cozystack.io
    apps.cozystack.io/application.kind: Postgres
    apps.cozystack.io/application.name: orders  # the view lists the account Secrets by these
    cnpg.io/reload: "true"                      # CNPG watches the Secret
  annotations:
    credentials.cozystack.io/origin: NotIssued
    credentials.cozystack.io/history: '[{"operation":"Create","at":"2026-10-02T09:10:02Z","by":"jane@example.org"}]'
data:
  username: web
  password: SCRAM-SHA-256$4096:...            # base64 in the real object
```

Then the API writes the HelmRelease `postgres-orders` and adds it to the Secret as owner. No ApplicationDefinition selects the Secret and no Role grants it, so no tenant can read it.

## 3. The chart renders the roles

The chart puts the marker role, the database roles and the user under CNPG's declarative role management. The user gets the flags it has in values, the marker as a parent, and its account Secret. Before Helm applies this `Cluster`, a pre-upgrade hook has checked that no role named `web`, `shop_admin` or `shop_readonly` exists without the marker, which matters once a user has held `createRole` ([below](#a-name-that-a-role-already-holds)).

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: postgres-orders
  namespace: tenant-acme
spec:
  managed:
    roles:
    - name: cozystack:managed
      login: false
    - name: shop_admin
      login: false
      inherit: false
      comment: role managed by helm
      inRoles: [cozystack:managed]
    - name: shop_readonly
      login: false
      inherit: false
      comment: role managed by helm
      inRoles: [cozystack:managed]
    - name: web
      login: true
      inherit: true
      replication: false
      createrole: false
      bypassrls: false
      comment: user managed by helm
      inRoles: [shop_admin, cozystack:managed]
      passwordSecret:
        name: postgres-orders.web.account
```

The init-job creates the database `shop`, hands it to `shop_admin`, sets object privileges and extensions, and sets no password or attribute. Once `web` and the database roles exist it grants the marker to them again `WITH INHERIT FALSE, SET FALSE`, since CNPG grants it with the default options.

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

The tenant updates the application with `users: {}`. The API writes the new HelmRelease, and the chart drops `web` from `managed.roles`, so CNPG stops managing it. The init-job drops the role `web`, because it carries the comment `user managed by helm` and the membership in `cozystack:managed`, and is no longer declared, after reassigning what it owns. Once the HelmRelease write has landed, the API deletes `postgres-orders.web.account`, and `kubectl get credentials` no longer lists the account.

## 12. The tenant deletes the application

Helm uninstalls the release, and Kubernetes garbage-collects the account Secrets together with the HelmRelease that owns them. The history of `web` survives in the audit log only. A new `orders` created later under the same name gets new Secrets with new UIDs, and nothing from the deleted one carries over.

## A name that a role already holds

A user of the instance that holds `createRole` can create a role under a name that an administrator declares later, and CNPG adopts a role by name, keeping its settings and the creator's `ADMIN OPTION`. So once any user has held `createRole`, the pre-upgrade hook refuses to let that happen. A service account of the application with `createRole` has created a role `reporting`, and later the tenant administrator adds `users.reporting`. The hook finds a role with no marker and fails the upgrade before any resource is updated, so the `Cluster` never lists `reporting` and CNPG never adopts it:

```
role "reporting" already exists and this chart did not create it; a user of this instance has held createRole, so the role may be controlled by that user and users.reporting will not adopt it. A platform administrator must inspect the role and drop it as the superuser, or declare the user under another name
```

The same check covers `<db>_admin` and `<db>_readonly` for every database. It is off until a user has held `createRole`, which the hook records in a setting of the `postgres` database before CNPG applies the attribute. Once a platform administrator drops the role, or the tenant picks another name, the next retry of the upgrade passes.

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
| Colliding users, or a postgres user named `app` | The converted chart refuses to render. The release keeps its previous revision, with old credentials working and exposed as before. A release left out for `app` stays there until the open question on `app` in the README is settled, since taking the user out of the values makes the init-job drop the role. |
| A MariaDB release without `<release>-credentials` | The converted chart keeps the root guard of today's chart and fails the upgrade, so the release keeps its previous revision. |
| ClickHouse account Secrets that do not exist | The HelmRelease names them in `valuesFrom`, Flux stops the release with `ValuesError`, and it keeps its previous revision. |
| A PostgreSQL release whose Secret was gone | The converted chart renders. CNPG leaves a role without an account Secret untouched, so the old passwords keep working, and tenants can no longer read them. A tenant mints a new one. |

Once an operator fixes the cause, running the Job again writes the account Secrets that are missing, from the plaintext in `<release>-credentials` while it is there, lets the release switch and finishes it. An account with no Secret and no plaintext is not listed, and a mint by name creates the Secret.

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
| PostgreSQL | `format: {name: scram-sha-256}`, `secretLabels: {cnpg.io/reload: "true"}`, `accounts: [{kind: ValuesMap, path: users}]` |
| MariaDB | `format: {name: mysql-native}`, `secretLabels: {k8s.mariadb.com/watch: ""}`, `accounts: [{kind: ValuesMap, path: users}, {kind: Static, name: root, owner: platform}]` |
| ClickHouse | `format: {name: sha256}`, `secretLabels: {reconcile.fluxcd.io/watch: Enabled}`, `valuesPath: _accounts`, `accounts: [{kind: ValuesMap, path: users}, {kind: Static, name: backup, owner: platform}]` |
| Redis | `format: {name: plaintext}`, `accounts: [{kind: Static, name: default, when: authEnabled}]` |

`owner` is `tenant` unless the entry says otherwise. MariaDB's `root` and ClickHouse's `backup` are `owner: platform`: they stay in `<release>-credentials`, and the API does nothing for them, so there is no `Credential` and no account Secret for either, and a mint cannot reach them. The entry also keeps a `users.root`, which the MariaDB chart ignores today, from being listed, seeded or migrated as a tenant account. ClickHouse's chart refuses its reserved names itself.

## A kind with no users: Redis

Redis has no `users`: one password, in `redis-cache-auth` for an application `cache`, which the chart generates with `lookup` and the operator reads through `auth.secretPath`. Its ApplicationDefinition declares that password as a fixed account, present while `authEnabled` is true:

```yaml
spec:
  credentials:
    format: {name: plaintext}   # the account Secret holds the password itself, as in wave 2
    accounts:
    - kind: Static
      name: default             # Credential redis-cache.default
      when: authEnabled         # the values key that switches the account on, true by default
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
