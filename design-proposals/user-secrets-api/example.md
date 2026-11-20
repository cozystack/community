# The contract for a kind, and a walkthrough

This file has two parts. [The contract for a kind](#the-contract-for-a-kind) shows what the chart of a kind renders, what the controller writes and what the API writes on a mint, for PostgreSQL, for ClickHouse, whose operator reads no Secret, and for a kind outside the tree that is not a database. The [walkthrough](#walkthrough-one-postgresql-application) follows one PostgreSQL application through the design in [README.md](./README.md), step by step, with the objects each step creates or changes. The README says why things work this way, and this file shows what happens.

## The contract for a kind

Three parties each have a fixed part. The chart of the kind renders one `CredentialTemplate` per account, with ordinary Helm logic, and what the engine or the pod needs, and nothing secret. The controller in cozystack-controller seeds the account Secret of each template and mirrors its record into the template's status. The Cozystack API serves the `Credential` view from the templates, writes an account Secret only on a mint or a revoke, and knows nothing of the kind. The ApplicationDefinition has no part in credentials: it carries no block for them, and its `secrets.include` no longer selects `<release>-credentials`.

Three kinds follow, and together they show every part a kind uses:

| Part | Shown in |
|---|---|
| a template per key of `users`, rendered with `range` | all three |
| a built-in `format` | PostgreSQL (`scram-sha-256`), ClickHouse (`sha256`) |
| a `format` registered as a `CredentialFormat` | nginx (`apr1`) |
| `spec.secret.labels` | PostgreSQL, ClickHouse |
| a platform account with no template, and the platform's own Secret | ClickHouse |
| `spec.valuesPath` and the `valuesFrom` entry the controller adds | ClickHouse |
| one fixed account behind an `if`, and `format: plaintext` | [Redis](#a-kind-with-no-users-redis), below |
| the engine reads the account Secret | PostgreSQL |
| the pod reads the account Secret | nginx |
| the chart gets the verifier as a value | ClickHouse |

### PostgreSQL

What the definition keeps. It has no `credentials` block, and the one change is the entry for `<release>-credentials` leaving `secrets.include`:

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
    prefix: postgres-            # the release is postgres-<name>, and the templates are named after it
    chartRef: {kind: ExternalArtifact, name: cozystack-postgres-application-default-postgres, namespace: cozy-system}
  secrets:
    include:
    - matchLabels: {internal.cozystack.io/tenant-ca: "true"}   # the public trust anchor; the entry for <release>-credentials is gone
```

What the chart renders. One template per key of `users`. PostgreSQL has no fixed or platform account, so nothing else gets a template:

```yaml
{{- range $user, $_ := .Values.users }}
---
apiVersion: cozystack.io/v1alpha1
kind: CredentialTemplate
metadata:
  name: {{ $.Release.Name }}.{{ include "postgres.accountName" $user }}   # <release>.<mapped user>
spec:
  username: {{ $user | quote }}          # the original name, immutable
  format: {name: scram-sha-256}          # the account Secret holds a SCRAM verifier, which CNPG stores as it is
  secret:
    labels: {cnpg.io/reload: "true"}     # CNPG notices a changed Secret only with this label
{{- end }}
```

`postgres.accountName` lowercases the user name and replaces every character outside `[a-z0-9-]` with `-`, and the render fails when two users map to the same name or to one that is not a valid DNS label.

And what it must not render. No `lookup`, no `randAlphaNum`, no Secret or init-script holding a password, and no Role or `secrets` entry that names an account Secret. PostgreSQL has no platform account, so after the migration there is no `<release>-credentials` at all. A kind that has one renders that Secret with `internal.cozystack.io/tenantresource: "false"` (README §4). The chart cannot read an account Secret at render time, so it only names it, and CNPG reads it at runtime through `passwordSecret`, which the reload label keeps current. The template and the role take the name from the same helper, so the Secret CNPG looks for is the one the controller writes. [Step 3](#3-the-chart-renders-the-roles) has the whole rendered `managed.roles`, and this is the part that names the Secret:

```yaml
{{- range $user, $_ := .Values.users }}
- name: {{ $user }}
  login: true
  passwordSecret:
    name: {{ $.Release.Name }}.{{ include "postgres.accountName" $user }}.account   # <template>.account
{{- end }}
```

What the controller writes. On a release whose HelmRelease carries `credentials.cozystack.io/seed: "true"`, which the API sets on every HelmRelease it creates, one Secret per template that has none. The Secret for `web` is the one in [step 2](#2-the-chart-renders-the-template-and-the-controller-seeds-the-account-secret), and every field of it is fixed:

- `type: kubernetes.io/basic-auth`
- the name `postgres-orders.web.account`, the template's name and `.account`
- the label `credentials.cozystack.io/account: "true"`, which a template cannot set, and `cnpg.io/reload: "true"` from the template's `secret.labels`
- the annotations `credentials.cozystack.io/origin` (`NotIssued`, `Migrated`, `Minted` or `Revoked`), `credentials.cozystack.io/history` (a JSON list of the last ten `{operation, at, by}`, newest first) and the name of the format the value is stored in
- the data keys `username`, the template's `spec.username`, and `password`, the SCRAM verifier of a random value that the controller discarded
- the template as owner

The controller then mirrors the record and the Secret's `resourceVersion` into the template's status. It never writes a Secret it did not create or adopt: a foreign Secret at that name leaves the template with a `SecretConflict` condition.

What the API writes on a mint. It builds the whole Secret from the template, with the verifier of a new password and the record from the template's status extended by the mint, and writes it under the Secret version that status mirrors, without reading the Secret. A stale status or a precondition naming another version fails with a 409 and no password. On success it returns the plaintext once ([step 6](#6-the-administrator-mints-a-password)).

### A kind outside the tree: nginx `auth_basic`

`MyApp` is a web application behind nginx `auth_basic`, and its kind lives outside the repository. It is not a database, so no operator reads the account Secret and the pod does. `apr1`, the htpasswd hash, is not one of the four built-in formats of README §3, so a platform administrator registers it as a `CredentialFormat`, a cluster-scoped webhook registration that no tenant can write. A template only names a format, never an endpoint.

```yaml
apiVersion: cozystack.io/v1alpha1
kind: CredentialFormat
metadata:
  name: apr1                     # the name a template uses in format.name; built-in names cannot be registered
spec:
  clientConfig:                  # the client configuration every Kubernetes webhook uses
    service: {namespace: myapp-system, name: apr1-format, path: /format}
    caBundle: <base64 PEM>
```

The webhook gets the format name, the username and the plaintext and returns the value to store. The controller calls it when it seeds and the API when it mints, and any failure writes nothing: a mint answers an error, and the controller retries the seed. The migration's CLI has only the built-in formats, so `MyApp` converts only new applications or ships its own migration.

What the definition keeps. No `credentials` block, and nothing credential-bearing is tenant-visible:

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
  secrets: {include: []}
```

What the chart renders. One template per key of `users`, naming the registered format and no labels, since nothing but the pod reads the Secrets:

```yaml
{{- range $user, $_ := .Values.users }}
---
apiVersion: cozystack.io/v1alpha1
kind: CredentialTemplate
metadata:
  name: {{ $.Release.Name }}.{{ include "myapp.accountName" $user }}
spec:
  username: {{ $user | quote }}
  format: {name: apr1}           # the CredentialFormat above
{{- end }}
```

And what it must not render: the same rules, no `lookup`, no `randAlphaNum`, no Secret with a password in it, and nothing in the dashboard resource map that grants a Secret. The chart cannot read the account Secrets at render time, so the pod reads them at runtime: one Secret volume per template, and a sidecar that composes the file nginx reads. The volume takes its name from the same helper as the template.

```yaml
volumes:
  {{- range $user, $_ := .Values.users }}
  - name: account-{{ include "myapp.accountName" $user }}
    secret:
      secretName: {{ $.Release.Name }}.{{ include "myapp.accountName" $user }}.account   # <template>.account
      optional: true            # the controller seeds it after the render, and the pod must not wait for it
  {{- end }}
  - {name: auth, emptyDir: {}}
containers:
  - name: nginx                 # auth_basic_user_file /etc/nginx/auth/htpasswd, re-read per request
    volumeMounts: [{name: auth, mountPath: /etc/nginx/auth, readOnly: true}]
  - name: htpasswd-sync         # writes "<username>:<password>" from the two keys of each mounted account, renames over htpasswd, loops on change
```

What the controller writes. One Secret per template, with the value the webhook returned for a random password it discarded. For `users: {alice: {}, Bob.Smith: {}}` the second is:

```yaml
apiVersion: v1
kind: Secret
type: kubernetes.io/basic-auth
metadata:
  name: myapp-site.bob-smith.account            # <template>.account
  namespace: tenant-acme
  labels:
    credentials.cozystack.io/account: "true"
  ownerReferences: [{kind: CredentialTemplate, name: myapp-site.bob-smith}]   # apiVersion and uid omitted
  annotations:
    credentials.cozystack.io/origin: NotIssued
    credentials.cozystack.io/history: '[{"operation":"Create","at":"...","by":"system:serviceaccount:cozy-system:cozystack-controller"}]'
    credentials.cozystack.io/format: apr1
data:
  username: Bob.Smith                           # the original name
  password: $apr1$...                           # hash of a random value the controller discarded
```

Nobody can log in until a mint. `POST .../credentials/myapp-site.bob-smith/mint` calls the webhook with the new password, writes the whole Secret under the version the template's status mirrors and returns the plaintext once. The mounted file changes on the kubelet's sync period, the sidecar rewrites `htpasswd`, and the new password works. `kubectl get credentials -n tenant-acme` lists both accounts with their origin, `-o yaml` adds the history, and neither shows a password.

### ClickHouse: an operator that reads no Secret, and a platform account

ClickHouse differs from the two kinds above in two ways. Its operator watches no Secrets, and the one live path to a user's password is a hash in the `ClickHouseInstallation`, which Helm renders, so the chart gets the verifier as a value and the HelmRelease pulls it from the account Secret. And it has a platform account, `backup`, which the backup jobs use and no tenant may see.

What the definition keeps. No `credentials` block, and `secrets.include` loses the entry for `<release>-credentials`, since that Secret now holds only `backup`:

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
  secrets: {include: []}
```

What the chart renders. One template per key of `users`, and none for `backup`, so `backup` has no `Credential`, no account Secret and no mint:

```yaml
{{- range $user, $_ := .Values.users }}
---
apiVersion: cozystack.io/v1alpha1
kind: CredentialTemplate
metadata:
  name: {{ $.Release.Name }}.{{ include "clickhouse.accountName" $user }}
spec:
  username: {{ $user | quote }}
  format: {name: sha256}                               # ClickHouse stores an unsalted SHA-256 hex, which is the value in the account Secret
  secret:
    labels: {reconcile.fluxcd.io/watch: Enabled}       # Flux reconciles the HelmRelease when an account Secret changes
  valuesPath: _accounts                                # the operator cannot read Secrets, so the verifiers reach the chart as values under this key
{{- end }}
```

And what it must not render: for tenant accounts the same rules as before, no `lookup`, no `randAlphaNum`, no password in any rendered object, and no Role or `secrets` entry that names an account Secret. The chart takes the verifier of each declared user from `.Values._accounts`, under the key `u<digest>`, where `<digest>` is the SHA-256 of the user name as `users` lists it. A user name fits neither a `targetPath` nor a value, since a comma fails the entry, while a hex digest fits both and leaves no order to keep aligned. An account with no verifier in the values, as on the first render before the controller has seeded it, is left out of the CHI, because the operator would give such an account the password `default`. The chart also refuses tenant accounts named `backup`, `default` or `clickhouse_operator`. `backup` stays as it is today: the chart generates it with `lookup`, keeps it in `<release>-credentials`, which it renders with `internal.cozystack.io/tenantresource: "false"`, and the backup CronJob and the sidecar read it from there.

```yaml
configuration:
  users:
    {{- range $name, $u := .Values.users }}
    {{- $verifier := get ($.Values._accounts | default dict) (printf "u%s" (sha256sum $name)) }}
    {{- if $verifier }}                                # no verifier yet: the account stays out of the CHI
    {{ $name }}/password_sha256_hex: {{ $verifier }}
    {{ $name }}/profile: {{ ternary "readonly" "default" ($u.readonly | default false) }}
    {{ $name }}/networks/ip: ["::/0"]
    {{- end }}
    {{- end }}
    backup/password_sha256_hex: {{ sha256sum $backupPassword }}   # from the lookup of <release>-credentials, as today
```

What the controller writes. For a release `clickhouse-reports` with `users: {analytics: {readonly: true}}` it seeds the account Secret, with the same fields as in the PostgreSQL case, the Flux label in place of the CNPG one, and a SHA-256 hex in `password`:

```yaml
apiVersion: v1
kind: Secret
type: kubernetes.io/basic-auth
metadata:
  name: clickhouse-reports.analytics.account
  namespace: tenant-acme
  labels:
    credentials.cozystack.io/account: "true"
    reconcile.fluxcd.io/watch: Enabled          # from the template's secret.labels
  ownerReferences: [{kind: CredentialTemplate, name: clickhouse-reports.analytics}]   # apiVersion and uid omitted
  annotations:
    credentials.cozystack.io/origin: NotIssued
    credentials.cozystack.io/history: '[{"operation":"Create","at":"...","by":"system:serviceaccount:cozy-system:cozystack-controller"}]'
    credentials.cozystack.io/format: sha256
data:
  username: analytics
  password: <64 hex digits>                     # SHA-256 of a random value the controller discarded
```

Because the template sets `valuesPath`, the controller also adds one `valuesFrom` entry for it to the HelmRelease when it seeds the Secret, and removes the entry when the template goes. The API keeps every entry other than `cozystack-values` when it writes the HelmRelease on an update, so an application update does not drop them. The digest below is the SHA-256 of `analytics`. The entry is optional, so a release still renders while a Secret is missing.

```yaml
spec:
  valuesFrom:
  - {kind: Secret, name: cozystack-values}      # as today, written by the API
  - kind: Secret                                # added by the controller
    name: clickhouse-reports.analytics.account
    valuesKey: password
    targetPath: _accounts.u65f98121a162a56ad8ee919ed9ea394b9eabd8714616acf30636c6092ee350b9
    optional: true
```

What the API writes on a mint: the whole Secret, with a new `password`, under the version the template's status mirrors. The label makes Flux reconcile the HelmRelease, helm-controller reads `valuesFrom` again and upgrades the release with the new hash, the operator rewrites the `chi-<chi>-common-usersd` ConfigMap, and ClickHouse re-reads it within seconds. So a mint costs a release upgrade, and a suspended or failing release holds it until the release recovers. `kubectl get credentials` lists `analytics` and never `backup`. The migration rotates `backup` once: the Job writes a new value into `<release>-credentials` and forces an upgrade, which applies its hash and restarts the ClickHouse pods once for the sidecar (README §4).

That is the whole contract for a kind. PostgreSQL's page differs from nginx's where CNPG reads `passwordSecret` and the reload label takes the place of the volume and the sidecar, and ClickHouse's where the template asks for a `valuesFrom` entry and the verifier travels through it into the chart.

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

## 2. The chart renders the template, and the controller seeds the account Secret

The API writes one object, the HelmRelease `postgres-orders`, with the seeding mark it puts on every HelmRelease it creates. It writes no Secret.

```yaml
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: postgres-orders
  namespace: tenant-acme
  annotations:
    credentials.cozystack.io/seed: "true"     # the controller seeds the accounts of this release
```

Flux renders the chart, which renders one template for `web` next to the `Cluster` of step 3. Helm's annotations tell the view which release, and so which application, the template belongs to:

```yaml
apiVersion: cozystack.io/v1alpha1
kind: CredentialTemplate
metadata:
  name: postgres-orders.web
  namespace: tenant-acme
  uid: 3f9c2e1a-5b7d-4c0e-9a61-0d2f8e4b7c35   # the account's identity
  annotations:
    meta.helm.sh/release-name: postgres-orders
    meta.helm.sh/release-namespace: tenant-acme
spec:
  username: web
  format: {name: scram-sha-256}
  secret:
    labels: {cnpg.io/reload: "true"}
```

The controller sees a template without a Secret on a marked release and seeds one. The password under `password` is the SCRAM verifier of a random value that the controller threw away, so the account can exist in the database while nobody can log in as it.

```yaml
apiVersion: v1
kind: Secret
type: kubernetes.io/basic-auth
metadata:
  name: postgres-orders.web.account
  namespace: tenant-acme
  resourceVersion: "48190"
  labels:
    credentials.cozystack.io/account: "true"
    cnpg.io/reload: "true"                      # from the template; CNPG watches the Secret
  ownerReferences: [{kind: CredentialTemplate, name: postgres-orders.web}]   # apiVersion and uid omitted
  annotations:
    credentials.cozystack.io/origin: NotIssued
    credentials.cozystack.io/history: '[{"operation":"Create","at":"2026-10-02T09:10:04Z","by":"system:serviceaccount:cozy-system:cozystack-controller"}]'
    credentials.cozystack.io/format: scram-sha-256
data:
  username: web
  password: SCRAM-SHA-256$4096:...            # base64 in the real object
```

Then it mirrors the record and the Secret's version into the template's status:

```yaml
status:
  secretResourceVersion: "48190"
  password: {origin: NotIssued, at: "2026-10-02T09:10:04Z", by: "system:serviceaccount:cozy-system:cozystack-controller"}
  history:
  - {operation: Create, at: "2026-10-02T09:10:04Z", by: "system:serviceaccount:cozy-system:cozystack-controller"}
```

No ApplicationDefinition selects the Secret and no Role grants it or the template, so no tenant can read the Secret or write the template.

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

The `Cluster` and the template arrive in the same render, so CNPG may see `passwordSecret` before the controller has seeded the Secret. It skips the role until the Secret exists, and the reload label makes it act once it does. The instance manager on the primary then creates `web` with the seeded verifier and reports the version of the Secret it applied.

```yaml
status:
  managedRolesStatus:
    passwordStatus:
      web:
        resourceVersion: "48190"              # the account Secret's resourceVersion
        transactionID: 812
```

## 5. The tenant sees the account, not a password

Any tenant member at `view` or above can list the accounts. The view is built from the templates and never reads a Secret, and its `resourceVersion` is the Secret version the template's status mirrors, `48190` here.

```
$ kubectl get credentials -n tenant-acme
NAME                  APPLICATION       USER   ORIGIN
postgres-orders.web   Postgres/orders   web    NotIssued
```

## 6. The administrator mints a password

The dashboard mints as soon as the account is listed after step 1, so the person who created the application sees the password once, in the creation dialog. The account appears once Flux has rendered the chart, seconds after the HelmRelease. A mint before the template exists answers NotFound, and one after the template but before the seed creates the Secret itself. Later mints come from the Regenerate button, `kubectl create --raw` or any client.

```
POST /apis/core.cozystack.io/v1alpha1/namespaces/tenant-acme/credentials/postgres-orders.web/mint
{"apiVersion": "core.cozystack.io/v1alpha1", "kind": "CredentialRequest", "spec": {"preconditions": {"resourceVersion": "48190"}}}

201 Created
{"apiVersion": "core.cozystack.io/v1alpha1", "kind": "CredentialRequest", "status": {"username": "web", "password": "<32 characters, shown once>", "issuedAt": "2026-10-02T09:14:07Z"}}
```

Inside the mint the API read the template `postgres-orders.web`, generated the 32 characters and computed their verifier. It built the whole `postgres-orders.web.account` from the template, with that verifier and the record from the template's status extended by this mint, and wrote it as an update conditioned on `resourceVersion: "48190"`, the version the template's status mirrors and the caller's precondition names. It never read the Secret. Then it put the plaintext into the response. The Secret now reads:

```yaml
metadata:
  name: postgres-orders.web.account
  resourceVersion: "48240"
  annotations:
    credentials.cozystack.io/origin: Minted
    credentials.cozystack.io/history: '[{"operation":"Mint","at":"2026-10-02T09:14:07Z","by":"jane@example.org"},{"operation":"Create","at":"2026-10-02T09:10:04Z","by":"system:serviceaccount:cozy-system:cozystack-controller"}]'
    credentials.cozystack.io/format: scram-sha-256
data:
  username: web
  password: SCRAM-SHA-256$4096:...            # the verifier of the new password
```

The plaintext is in no object. If the response is lost, nobody holds this password, and the next call replaces it. The controller then mirrors version `48240` and the new record into the template's status. Until it does, the view shows the previous record, and a mint in that window fails with a 409 and no password, so the caller retries.

## 7. CNPG applies the new verifier

The reload label makes CNPG notice the change. The instance manager runs `ALTER ROLE web PASSWORD 'SCRAM-SHA-256$...'`, and PostgreSQL stores the verifier as it is. CNPG reports `resourceVersion: "48240"` for `web`, and the new password works from then on. The `Credential`, once the controller has mirrored the Secret into the template, shows the record only, and says nothing about the engine:

```yaml
apiVersion: core.cozystack.io/v1alpha1
kind: Credential
metadata:
  name: postgres-orders.web
  namespace: tenant-acme
  uid: 3f9c2e1a-5b7d-4c0e-9a61-0d2f8e4b7c35   # the template's UID
  resourceVersion: "48240"                    # the Secret version the template's status mirrors
spec:
  application: {apiGroup: apps.cozystack.io, kind: Postgres, name: orders}
  user: web
status:
  password: {origin: Minted, at: "2026-10-02T09:14:07Z", by: jane@example.org}
  history:
  - {operation: Mint, at: "2026-10-02T09:14:07Z", by: jane@example.org}
  - {operation: Create, at: "2026-10-02T09:10:04Z", by: "system:serviceaccount:cozy-system:cozystack-controller"}
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

The response carries no password. The API builds the Secret from the template again, with the verifier of a new random password, writes it the same way as a mint and throws that password away. CNPG applies it, and `web` can no longer log in with any password anyone holds. The origin becomes `Revoked`, the audit log shows a `create` on the `revoke` subresource, and the Event reads `CredentialRevoked`. Connections that were already open stay open. Getting a working password back is step 6 again.

## 11. The tenant removes the user

The tenant updates the application with `users: {}`. The API writes the new HelmRelease and nothing else. The next render drops `web` from `managed.roles`, so CNPG stops managing it, and no longer renders the template `postgres-orders.web`, so Helm deletes it and the garbage collector deletes `postgres-orders.web.account`, which the template owns. The init-job drops the role `web`, because it carries the comment `user managed by helm` and the membership in `cozystack:managed`, and is no longer declared, after reassigning what it owns. `kubectl get credentials` no longer lists the account.

## 12. The tenant deletes the application

The API deletes the HelmRelease, Helm uninstalls the release and with it the templates, and Kubernetes garbage-collects the account Secrets together with the templates that own them. The history of `web` survives in the audit log only. A new `orders` created later under the same name gets new templates and new Secrets with new UIDs, and nothing from the deleted one carries over.

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

The release that converts PostgreSQL runs a hook before any chart changes. It records the chart version of the release, writes `postgres-orders.web.account` with the verifier of that same password, origin `Migrated`, the account label and no owner, and marks the HelmRelease `postgres-orders` with `credentials.cozystack.io/seed: "true"`. It touches nothing else, so the old chart keeps working if it renders before the switch. Then the new chart renders the template `postgres-orders.web` and the `managed.roles` of step 3. The controller finds an unowned Secret at the template's name with the account label and the same `username`, and adopts it with its value and record instead of seeding a new one. CNPG takes over `web` and applies the verifier of the same password, so every client keeps working. Once the release is Ready on a chart version other than the recorded one, the Job removes a `users.web.password` if one was left in the HelmRelease values and deletes `postgres-orders-credentials`, since PostgreSQL keeps no platform account there. The record shows `Migrated` until someone mints or revokes, and older Helm revisions keep the old plaintext until `MaxHistory` drops them.

## When the migration leaves a release out

A release the migration cannot convert is reported in the migration log and with an Event on its HelmRelease, and it carries no seeding mark, so the controller does not replace the passwords its clients use. The cause decides what it does once the converted charts arrive.

| Cause | What happens |
|---|---|
| Colliding users, or a postgres user named `app` | The converted chart refuses to render. The release keeps its previous revision, with old credentials working and exposed as before. |
| A MariaDB release without `<release>-credentials` | The converted chart keeps the root guard of today's chart and fails the upgrade, so the release keeps its previous revision. |
| A ClickHouse release whose `<release>-credentials` was gone | The converted chart renders its templates. The controller seeds no Secret and adds no `valuesFrom` entry, and the chart leaves every tenant account without a verifier out of the CHI. |
| A PostgreSQL release whose Secret was gone | The converted chart renders. CNPG leaves a role without an account Secret untouched, so the old passwords keep working, and tenants can no longer read them. The view lists the accounts as `NotIssued`, and a tenant mints a new password. |

Once an operator fixes the cause, running the Job again writes the account Secrets that are missing, from the plaintext in `<release>-credentials` while it is there, marks the release for seeding, lets the release switch and finishes it. A template with no Secret is listed as `NotIssued`, and a mint creates the Secret.

## How MariaDB and ClickHouse differ

| Step | MariaDB | ClickHouse |
|---|---|---|
| 2, the template and the account Secret | the template names `mysql-native` and the label `k8s.mariadb.com/watch`, so `password` holds `PASSWORD()` output (`*` and 40 hex digits) | the template names `sha256`, the label `reconcile.fluxcd.io/watch: Enabled` and `valuesPath: _accounts`, so `password` holds the SHA-256 hex, and the controller adds an optional `valuesFrom` entry for the Secret to the HelmRelease, keyed by a digest of the user name |
| 3, the chart | the `User` points `passwordHashSecretKeyRef` at the account Secret | the chart renders `password_sha256_hex` into the CHI from the value that entry supplies, and leaves out an account with no value yet |
| 7, applying a new value | the operator re-runs `ALTER USER` | helm-controller upgrades the release, the operator rewrites `chi-<chi>-common-usersd`, and ClickHouse re-reads it within seconds |
| Conversion | `<release>-credentials` keeps `root`, which the Job rotates with `ALTER USER` | `<release>-credentials` keeps `backup`, which the Job rotates, restarting the ClickHouse pods once |

## How the kinds declare their accounts

Each chart decides its accounts with ordinary Helm logic and renders one template per account. No tenant role grants `CredentialTemplate`, so a tenant administrator, who can edit the application, changes its accounts only through its values and cannot change how their passwords are stored.

| Kind | What the chart renders |
|---|---|
| PostgreSQL | a template per key of `users`, with `format: {name: scram-sha-256}` and `secret.labels: {cnpg.io/reload: "true"}` |
| MariaDB | a template per key of `users`, with `format: {name: mysql-native}` and `secret.labels: {k8s.mariadb.com/watch: ""}`, and none for `root` |
| ClickHouse | a template per key of `users`, with `format: {name: sha256}`, `secret.labels: {reconcile.fluxcd.io/watch: Enabled}` and `valuesPath: _accounts`, and none for `backup` |
| Redis | one template, `default`, while `authEnabled` is true, with `format: {name: plaintext}` |

Platform accounts get no template. MariaDB's `root` and ClickHouse's `backup` stay in `<release>-credentials`, and neither the controller nor the API does anything for them, so there is no `Credential` and no account Secret for either, and a mint cannot reach them. A `users.root`, which the MariaDB chart ignores today, gets no template either, or it would be listed as a tenant account. ClickHouse's chart refuses its reserved names itself.

## A kind with no users: Redis

Redis has no `users`: one password, in `redis-cache-auth` for an application `cache`, which the chart generates with `lookup` and the operator reads through `auth.secretPath`. After the conversion the chart renders one fixed account behind an `if` on the value that switches authentication on:

```yaml
{{- if .Values.authEnabled }}             # true by default
apiVersion: cozystack.io/v1alpha1
kind: CredentialTemplate
metadata:
  name: {{ .Release.Name }}.default       # Credential redis-cache.default
spec:
  username: default
  format: {name: plaintext}               # the account Secret holds the password itself, as in wave 2
{{- end }}
```

The chart stops generating the password and points the operator at the account Secret that the controller seeds, whose `password` key has the name the operator reads today:

```yaml
  {{- if .Values.authEnabled }}
  auth:
    secretPath: {{ .Release.Name }}.default.account
  {{- end }}
```

The account starts as `NotIssued`, and a mint replaces the password in that Secret. Turning `authEnabled` off removes the template on the next render, and the garbage collector removes the Secret. How a changed password reaches the replicas and the sentinels is the Redis row of §3 at its conversion.

## Headless provisioning and Terraform

A pipeline with no human in it creates the application and waits until its accounts are listed, which happens once Flux has rendered the chart, seconds after the HelmRelease. They start as `NotIssued`, and a mint before the template exists answers NotFound. It mints from the tenant ServiceAccount, which holds the verb already, and stores the response where the workload reads it, a Secret of the tenant or its own password store. Storing is the caller's, so the platform guarantees that it holds no plaintext, not that the tenant keeps none. A GitOps helper or operator that mints and writes the result into the tenant's external password store can be built on this call, and is later work.

A Terraform resource that wraps mint keeps the plaintext in state, as `aws_iam_access_key` does. That is the caller's choice and not for the strictest policies. `import` returns the metadata and no password, and a mint made elsewhere shows in `status.password.at` and `by`, which the provider reads as drift.
