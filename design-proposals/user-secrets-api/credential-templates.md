# Credential templates

This is the design behind [decision record 0002](./decisions/0002-account-list-comes-from-chart-rendered-credential-templates.md), for the rework of cozystack/cozystack#4921 and #4922. It replaces §1 (who writes account Secrets), the declaration half of §7 (the ApplicationDefinition `credentials` block) and the wiring tables of §3 of the user-secrets-api proposal. Everything else in the proposal stands: verifiers in the account Secret, `mint` and `revoke` as subresources in the shape of TokenRequest, the `Credential` view, audit through kube-apiserver, tenant RBAC, the conversion through a numbered migration.

## The rule

An aggregated API request writes at most one backend object. Anything that keeps several objects in step is a controller. The one exception is the best-effort Event a mint or revoke records on the application.

## Who does what

| Step | Done by | Objects written |
|---|---|---|
| Tenant creates, updates or deletes an application | Cozystack API | the HelmRelease |
| Which accounts exist, and how their Secret looks | the kind's chart | one `CredentialTemplate` per account, applied by Flux with the release |
| Account Secret appears, `NotIssued` | cozystack-controller | the Secret `<template>.account` |
| Account Secret goes away | garbage collector | ownerReference on the template |
| `mint`, `revoke` | Cozystack API | that one Secret |
| `Credential` get and list | Cozystack API | nothing |
| Password reaches the engine | the engine's operator | its own objects |

## CredentialTemplate

Group `cozystack.io/v1alpha1`, next to ApplicationDefinition and SchedulingClass, packaged with the CRDs in `application-definition-crd`. `core.cozystack.io/v1alpha1` is served whole by the aggregated API, so a CRD cannot live there. Namespaced, no status.

```yaml
apiVersion: cozystack.io/v1alpha1
kind: CredentialTemplate
metadata:
  name: postgres-orders.web            # <release>.<user mapped to a DNS label>
  namespace: tenant-acme
spec:
  username: web                        # immutable; shown in Credential and in the mint answer
  secret:                              # the Secret is always <metadata.name>.account, same namespace
    labels:
      cnpg.io/reload: "true"           # what the engine's operator needs to notice a change
    data:                              # key -> template; [[ ]] delimiters, so a Helm chart needs no escaping
      username: '[[ .Username ]]'
      password: '[[ scramSha256 .Password ]]'
```

Template language: Go `text/template` with `[[ ]]` delimiters, the sprig functions Helm ships minus those that read the environment or the filesystem (`env`, `expandenv`, `getHostByName`), plus the format functions from `pkg/credentials/format` (`scramSha256`, `mysqlNative`, `sha256Hex`; `bcrypt` and `htpasswd` come from sprig). Inputs are `.Username` and `.Password` only. Output is capped in size and the render in time. A render error is reported without the input, so a template bug cannot echo a password.

The application a template belongs to comes from the HelmRelease that rendered it (`meta.helm.sh/release-name` on the template, the application labels on that HelmRelease). A template whose release is gone is ignored.

## The account Secret

```yaml
apiVersion: v1
kind: Secret
type: Opaque
metadata:
  name: postgres-orders.web.account
  namespace: tenant-acme
  labels:
    credentials.cozystack.io/account: "true"
    cnpg.io/reload: "true"                       # copied from the template
  ownerReferences:
    - {apiVersion: cozystack.io/v1alpha1, kind: CredentialTemplate, name: postgres-orders.web, uid: ..., controller: true}
  annotations:
    credentials.cozystack.io/origin: Minted      # NotIssued | Migrated | Minted | Revoked
    credentials.cozystack.io/at: "2026-11-19T09:14:07Z"
    credentials.cozystack.io/by: jane@example.org
    credentials.cozystack.io/history: '[...]'    # last ten operations, no values
    credentials.cozystack.io/template-hash: 9f2c...   # hash of spec.secret.data the value was rendered with
data:
  username: d2Vi
  password: U0NSQU0tU0hBLTI1NiQ...
```

The labels the API selects by (`credentials.cozystack.io/account`) are reserved: a template that sets one is refused by the CRD's validation.

## Controller

A reconciler on `CredentialTemplate` in cozystack-controller, which already holds CRUD on Secrets.

1. Secret missing: render with a random password from `crypto/rand`, discard the password, create the Secret owned by the template, origin `NotIssued`.
2. Secret exists, owned by this template: if `template-hash` differs from the template, render again with a discarded password and set origin `NotIssued`, record a `CredentialReset` Event on the template. Otherwise only bring the labels in line.
3. Secret exists, unowned, with the account label and the same `username`: adopt it (set the ownerReference and the current `template-hash`), keep its value and origin. This is how Secrets the conversion seeded before the templates existed survive the switch.
4. Secret exists and is anything else (owned by another object, or without the account label): leave it alone and set a `SecretConflict` condition on the template. The controller never overwrites a Secret it did not create or adopt.

Removing a template is Helm's job, and the Secret follows through garbage collection.

## API

`Credential` get and list read templates and their Secrets in the namespace. The view is unchanged: `spec.application` and `spec.user`, `status.password` and `status.history` from the Secret annotations, never the data. A template without its Secret yet is listed with origin `NotIssued`.

`mint` and `revoke`:

1. Read the template and its Secret. The Secret missing, or not owned by this template's UID: `409 Conflict`, retryable, "the account is not ready yet". No delete and no create in the request.
2. Precondition: the caller's `resourceVersion` differs from the Secret's: `409` naming who changed it, as today.
3. Dry run: answer without a password and write nothing, as today.
4. Generate 32 characters from `[A-Za-z0-9]`, render `spec.secret.data`, update the Secret once under its `resourceVersion` with the data, the record and `template-hash`. A conflicting update answers `409` without a password.
5. Record `CredentialIssued` or `CredentialRevoked` on the application, without the value. Return the password for a mint, nothing secret for a revoke.

Application create, update and delete write only the HelmRelease. The refusal to delete a converted application whose release is suspended stays: it reads, then deletes one object. "Converted" now means the live release carries templates.

## RBAC

| Subject | Gets |
|---|---|
| tenant `view` and above | `get`, `list` on `core.cozystack.io/credentials` (unchanged) |
| tenant `admin`, tenant ServiceAccount | `create` on `credentials/mint`, `credentials/revoke` (unchanged) |
| tenants | nothing on `cozystack.io/credentialtemplates`; tenant roles list `cozystack.io` resources explicitly today, and a test pins it |
| cozystack-api | `get`, `list`, `watch` on `credentialtemplates`; Secrets as today; `create` on Events. The `leases` grant goes |
| cozystack-controller | `get`, `list`, `watch` on `credentialtemplates`, `patch` on their status conditions; Secrets as today |

## Engines

**PostgreSQL** (#4922). The chart renders a template per user with `password: '[[ scramSha256 .Password ]]'` and the `cnpg.io/reload` label, and `managed.roles[].passwordSecret` names `<template>.account`. The controller seeds at once, so CNPG creates the role as it does today and the init-script stays as in #4922. `createRole` and `users.app` are decided separately.

**MariaDB.** One template per user with `password: '[[ mysqlNative .Password ]]'` and the `k8s.mariadb.com/watch` label; each `User` points `passwordHashSecretKeyRef` at it. `root` stays a platform account in `<release>-credentials`, as the proposal says.

**ClickHouse.** No `valuesFrom`. The template renders only the secret part of a user, `users.d/<user>.xml` with `<password_sha256_hex>`; the chart renders profiles, quotas and networks for the same users in a ConfigMap, which ClickHouse merges element by element. Both are mounted as one projected volume under a second `users_xml` storage in `user_directories`, which ClickHouse reloads on its own when kubelet updates the files. To be checked on a cluster before the ClickHouse conversion: that the operator keeps a pod template volume and does not override `user_directories`, the reload delay, and that tenant names never collide with the operator's users, since a user name in two storages resolves to the first.

**Aggregated files** (NATS, `htpasswd`). One Secret per account, combined where it is consumed: a projected volume and a reloader, or the engine's own include mechanism. Never a template that renders all users at once.

## Conversion

Migration 61 stays. `seed` writes `<release>.<user>.account` with the verifier of the legacy password, origin `Migrated`, the account label and no owner. After the charts switch, the controller adopts those Secrets (step 3). The finish Job is unchanged (decision record 0001). The verifier the migration writes must be one the template would accept for the same password: for SCRAM any valid salt does, for unsalted formats the CLI uses the same functions.

## What changes in the two pull requests

#4921 drops: `spec.credentials` in ApplicationDefinition with its types, CRD schema, deepcopy and OpenAPI; the account resolvers in `pkg/credentials/account`; `store.Ensure`, `Prune`, `Added`, `ValuesFrom`; `pkg/credentials/applock`; the seeding, unseeding and pruning in `rest_credentials.go`; the `valuesFrom` code in the ApplicationDefinition reconciler; the `leases` grant; their tests. It adds the CRD, the controller and the template renderer. It keeps the `Credential` types, `mint` and `revoke` (rewritten as above), `pkg/credentials/format` as template functions, the Event, the audit documentation, `fluent-bit.extraOutputs`, the API gate regex and the tenant RBAC.

#4922 changes the chart to render templates and drops the `credentials` block from `postgres-rd`; the rest stays, subject to the `createRole` and `users.app` decisions.

## Tests

- Template rendering: every format function against its reference, delimiters, refused functions, size and time caps, an error that carries no input.
- Controller: seed, reset on a changed template, adopt a migrated Secret, refuse a foreign Secret, garbage collection on template removal.
- Mint and revoke: one write per request (an interceptor counts writes), `409` on a missing or foreign Secret, precondition, dry run, no password in logs or Events.
- Application writes: one write per request for create, update and delete.
- RBAC: no tenant tier can create, update or delete a `CredentialTemplate`.
- Postgres e2e as in #4922, plus a user added and removed through the application with no API-side Secret writes.
