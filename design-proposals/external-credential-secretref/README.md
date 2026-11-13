# SecretRef for external credentials in managed application specs

- **Title:** `SecretRef for external credentials in managed application specs`
- **Author(s):** `@IvanHunters`
- **Date:** `2026-07-15`
- **Status:** Draft

## Overview

A managed application's credential today lives as a literal string inside the `apps.cozystack.io` custom resource spec. `spec.users[].password` on `postgres`, `mariadb`, `rabbitmq`, and `clickhouse` is a plaintext value the tenant types into the CR (`packages/apps/postgres/values.yaml:144-160` and siblings). The chart copies that value into a `<release>-credentials` Secret for the backing operator, but the literal also stays in the CR spec — and the CR spec is readable by every tenant subject that can `get` the app CR, which the default tenant role already grants (see Security). So a credential that the platform is careful to keep in a Secret for the operator is simultaneously exposed in the clear on `kubectl get postgres <name> -o yaml`. A pre-shared key or an external-system API token has no auto-generation fallback, so writing it inline is the only option the current model offers today — and that option is plaintext-in-spec. The site-to-site gateway work (`cozystack/community#30`) must not repeat that shape.

This proposal defines one uniform way for a managed-application spec to carry a **reference** to a credential instead of the credential itself: a `valueFrom.secretKeyRef`-shaped `{name, key}` selector that points at a Secret in the tenant namespace, which the chart wires straight into the backing operator's native secret-reference field. The value never enters the CR spec, so `get <app>` never leaks it. The mechanism is additive — the inline field stays valid — and it generalises two precedents that already exist in the tree (the `kubernetes` OIDC `secretRef` and the backup `s3CredentialsSecret.name`) into a single contract plus a `cozy-lib` helper, so every engine wires it the same way instead of each re-deriving it.

## Scope and related proposals

This proposal defines the **credential-reference contract** and the shared helper. It does not itself convert any engine; each conversion is a follow-up PR against `cozystack/cozystack` that consumes this contract.

- **Consumed by — engine password cleanup**: removing plaintext `users[].password` from `postgres`/`mariadb` in favour of auto-generation plus rotation. This proposal is the reference half of that work — the "how does a tenant supply a password without putting it in the spec" answer.
- **Consumed by — site-to-site gateway** (`cozystack/community#30`, `site-router` / `site-gateway`): IPsec PSK and BGP-MD5 are external secrets with no generation fallback; this contract is how they enter the app spec without becoming plaintext-in-spec. Reference-only credentials (a SecretRef plus a two-phase bootstrap) were called out as a requirement for that work.
- **Adjacent, not overlapping — `design-proposals/unified-tls-pki`**: that proposal delivers a key-free `ca.crt` **outward** to tenants over the TenantSecret channel. This one brings tenant-owned credential material **inward** to an operator. They share the platform mechanisms (the `internal.cozystack.io/tenantresource` label, the pinned `valuesFrom`, the `lookup` render-time limitation) and must agree on them, but they move secrets in opposite directions and define no shared resource.
- **Deferred:** backup S3 credentials already have `s3CredentialsSecret.name` and are out of scope (and marked deprecated in favour of the platform BackupClass flow, `postgres/values.yaml:214-217`). TLS private-key material is out of scope — it belongs to `unified-tls-pki`.

All repository paths below refer to `cozystack/cozystack` at `origin/main` (`3fb8ec5b0`) unless stated otherwise.

## Context

### How a credential flows today

Every SQL/queue engine uses the same `users` map: a plaintext `password` in the spec, auto-generated when the field is omitted.

- `postgres` — `users[].password`, `packages/apps/postgres/values.yaml:144-160`. Materialised into Secret `<release>-credentials` (`templates/init-script.yaml:18-27`) and applied by SQL `ALTER ROLE ... WITH PASSWORD` in the init job (`init-script.yaml:53`).
- `mariadb` — `users[].password`, `values.yaml:92-105`; into `<release>-credentials`, consumed by the operator via `rootPasswordSecretKeyRef` / `passwordSecretKeyRef` (`templates/mariadb.yaml:6-9`, `templates/user.yaml:13-15`).
- `rabbitmq` — `users[].password`, `values.yaml:91-101`; into `<release>-<kebabcase-user>-credentials`, consumed via `importCredentialsSecret.name` (`templates/rabbitmq.yaml:53-54`).
- `clickhouse` — `users[].password`, `values.yaml:88-100`; user passwords are written into the CHI spec as a render-time `password_sha256_hex` (`templates/clickhouse.yaml:46`); the env `secretKeyRef` at `:206-210` feeds only the chart-internal `backup` user, not tenant users.
- `redis` — no password field at all: `authEnabled: true` (`values.yaml:89-90`) generates a 32-char secret into `<release>-auth`, consumed via `auth.secretPath` (`templates/redisfailover.yaml:67-70`). This is already the correct model.

The auto-generation idiom is uniform and inline in every chart: `lookup` the existing Secret, reuse its value, else `randAlphaNum N`. Canonical example, `postgres/templates/init-script.yaml:1-27`:

```gotemplate
{{- $existingSecret := lookup "v1" "Secret" .Release.Namespace (printf "%s-credentials" .Release.Name) }}
{{- range $user, $u := .Values.users }}
  {{- if $u.password }}{{- $_ := set $passwords $user $u.password }}
  {{- else if not (index $passwords $user) }}{{- $_ := set $passwords $user (randAlphaNum 16) }}
  {{- end }}
{{- end }}
```

It is duplicated in `mariadb/templates/secret.yaml`, `clickhouse/templates/clickhouse.yaml`, `redis/templates/redisfailover.yaml`, `rabbitmq/templates/rabbitmq.yaml`, `mongodb/templates/user-secrets.yaml`, `opensearch/templates/users.yaml`, and `vpn/templates/secret.yaml`. There is no `cozy-lib` helper for it (see "Platform mechanisms").

### The two precedents that already reference an external Secret

Two fields in the tree already let a tenant point at a pre-existing Secret by name instead of inlining a value. They are the shape this proposal generalises.

1. **`kubernetes` OIDC** — the closest analog. `packages/apps/kubernetes/values.yaml:328-347`:

   ```yaml
   ## @field {OIDCSecretRef} [secretRef] - Reference to an existing Secret in the tenant
   ##   namespace carrying the AuthenticationConfiguration.
   oidc:
     customConfig:
       config: ""            # inline
       secretRef:
         name: ""            # OR reference — exactly one
   ```

   Resolution (`templates/cluster.yaml:318-323`) picks the referenced name over the chart default; the tenant-owned Secret is mounted onto the kube-apiserver directly and **the chart never copies its contents** (`cluster.yaml:314-316`); render `fail`s if both `config` and `secretRef.name` are set (`oidc-authn-config.yaml:80-84`). This is a fully working inline-or-reference credential field, decided per-render, with no content copy.

2. **Backup S3** — `s3CredentialsSecret.name` (`postgres/templates/db.yaml:34-35,117-123`) overrides the chart-managed `<release>-s3-creds` Secret; CNPG consumes it by `{name, key}`. Same override shape, applied to an operator CR field.

### The operator side, by engine

The engines fall into three classes by how a user credential reaches the workload — and the conversion cost differs sharply between them. This is the load-bearing distinction the contract must respect.

1. **Key-selectable reference — fits `{name, key}` directly.** `mariadb` `passwordSecretKeyRef{name,key}` / `rootPasswordSecretKeyRef` (`templates/mariadb.yaml:7-9`, `templates/user.yaml:13-15`) and `mongodb` `passwordSecretRef{name,key}` (`templates/mongodb.yaml:140`). The chart fills the reference with the fixed `<release>-credentials` name it created; pointing it at a tenant-supplied `{name, key}` is a small change.
2. **Name-only reference with a mandated key layout — fits a name-only variant.** `rabbitmq` `importCredentialsSecret.name` requires the referenced Secret to carry the keys `username` and `password` (`templates/rabbitmq.yaml:53-67`); `redis` `auth.secretPath` requires a `password` key (`templates/redisfailover.yaml:67-70`). Here the free `key` field is meaningless — the tenant Secret must follow the operator's required layout, so the contract must publish that layout per engine (Design §3).
3. **No user-credential reference field at all today — requires adopting a new operator mechanism.** `postgres` applies user passwords as a rendered SQL literal in the init job (`templates/init-script.yaml:53`), and CNPG runs with `enableSuperuserAccess: true` and no `managed.roles` (`templates/db.yaml:195`); `clickhouse` writes a render-time `password_sha256_hex` into the CHI spec (`templates/clickhouse.yaml:46`). Neither has a Secret-reference field for user passwords. Converting them means adopting a new operator path — realistically CNPG `managed.roles[].passwordSecret` (a `kubernetes.io/basic-auth` Secret) for postgres, and an equivalent secret-backed setting on the Altinity operator for clickhouse — each with its own required Secret shape. That is design and testing work, not a one-line change, and it is why `mariadb` (class 1), not `postgres`, is the reference implementation (Rollout).

The only places a tenant already overrides one of these with an external Secret today are the backup path (CNPG `s3Credentials.accessKeyId{name,key}` fed from `s3CredentialsSecret.name`) and the `kubernetes` OIDC `secretRef` — the two precedents above.

### Platform mechanisms this proposal builds on

- **The tenant read channel.** Secrets labelled `internal.cozystack.io/tenantresource: "true"` (`pkg/apis/core/v1alpha1/tenantresource_types.go:3-4`) surface to tenants as `core.cozystack.io/tenantsecrets` (`pkg/registry/core/tenantsecret/rest.go`). Tenant ServiceAccounts get `get/list/watch` on that virtual resource, never on raw `core/v1` Secrets (`packages/system/cozystack-basics/templates/clusterroles.yaml:45-51`).
- **The label's authority is the lineage webhook, not the chart.** `internal/lineagecontrollerwebhook/webhook.go:184-189` re-computes the `tenantresource` label on every Secret admission from the owning application's `spec.secrets` selector (`webhook.go:43-47` returns `&crd.Spec.Secrets` for a `Secret`; the `Secrets` field is `api/v1alpha1/applicationdefinitions_types.go:54-55`, the selector types at `:108-126`). A statically-stamped label does not survive unless it also matches a `spec.secrets.include` entry. This matters here only in the negative: a tenant-owned credential Secret must **not** be pulled into `spec.secrets`, or the platform starts treating a private input as a tenant-projected output.
- **`valuesFrom` is pinned.** `expectedValuesFrom()` (`internal/controller/applicationdefinition_helmreconciler.go:99-107`) hardcodes the single `{Kind: Secret, Name: cozystack-values}` reference and overwrites drift. A chart cannot add a sideways `valuesFrom` pointing at the tenant's credential Secret — so the credential's **content** cannot be injected into chart values. Only a **reference** can flow through values.
- **`lookup` is invisible to Flux.** `lookup` runs at template-render time and is not part of the HelmRelease digest, so a chart that reads a Secret via `lookup` does not re-render when that Secret's content changes or when it first appears. A design that resolved the credential at render time would silently miss a rotation or a late-created Secret.

These last two constraints are not obstacles to design around; they are the argument **for** reference-passing. The credential name is a string that rides through values harmlessly; the value is read by the operator or the pod at runtime, where rotation and late creation are observed natively.

### The problem

1. **Plaintext-in-spec.** A credential set inline lands in the `apps.cozystack.io` CR spec, which is readable by every tenant subject that can `get` it — a grant the default tenant role already carries (see Security). The platform's care to keep the value in a Secret for the operator is undone by the copy left in the spec. For engines with auto-generation this is a self-inflicted wound (the tenant did not have to inline anything); for external secrets it is unavoidable under the current model.
2. **No path for a generation-less external secret.** IPsec PSK, BGP-MD5, and external-integration tokens/logins cannot be auto-generated — their value is dictated by the peer or the third party. The current model offers only the inline field, i.e. plaintext-in-spec. `cozystack/community#30` needs an answer before it ships, and `vpn` (Outline/Shadowbox, `packages/apps/vpn/`) is not it — it has no PSK/IPsec/token field at all.
3. **No shared contract or helper.** The two existing reference fields (`kubernetes` OIDC, backup S3) were each hand-built, with different shapes and different validation. The generation idiom is copy-pasted across eight charts. There is nothing for a new engine to reuse, so each new credential re-derives the answer — and the per-app TLS PRs already show that re-derivation drifts.

## Goals

- A single value-schema type and rendering convention for "this credential is a reference to a tenant-namespace Secret, not a literal", reusable by any managed-application chart.
- The referenced value never appears in the `apps.cozystack.io` CR spec, and the chart never copies it into a chart-owned Secret; it is consumed by the operator/pod at runtime by `{name, key}`.
- Additive and backward-compatible: existing inline fields keep working; existing clusters that set inline passwords are unaffected until they opt in.
- A `cozy-lib` helper that both (a) centralises the generate-or-reuse idiom currently duplicated across eight charts and (b) resolves the inline-or-reference choice into the operator's native reference field, so every engine wires it identically.
- A defined two-phase bootstrap (tenant creates the Secret, then references it) with fail-closed behaviour when the referenced Secret is absent.

### Non-goals

- Not changing the default: auto-generation stays the default for engine user passwords; this adds a reference option, it does not force it.
- Not the plaintext-removal / rotation policy itself — that is the engine password-cleanup work, which consumes this contract but decides its own deprecation timeline.
- Not backup S3 credentials (already have `s3CredentialsSecret.name`, deprecated path).
- Not TLS private keys or `ca.crt` delivery (that is `unified-tls-pki`).
- Not a new CRD, a new controller, or a new aggregated-API resource.

## Design

### 1. The reference type

A shared value-schema `@typedef` mirroring the Kubernetes `SecretKeySelector` shape tenants already know from `env[].valueFrom`:

```yaml
## @typedef {struct} SecretKeyRef - Reference to a key in an existing Secret in the tenant (release) namespace.
## @field {string} name - Name of the Secret. Must exist in the application's own namespace.
## @field {string} key  - Key within the Secret whose value is the credential.
```

A credential field is expressed one of two ways depending on whether the engine can generate the value:

- **Generatable credential (engine user password).** The field accepts either the inline literal (unchanged) or a `valueFrom.secretKeyRef`. Exactly one; render `fail`s if both are set, matching the OIDC precedent (`oidc-authn-config.yaml:80-84`). Omitting both keeps today's auto-generation.

  ```yaml
  users:
    app1:
      # password: hackme                 # inline (still valid; discouraged)
      passwordSecretRef:                  # OR reference
        name: app1-db-password
        key:  password
  ```

- **Non-generatable external secret (PSK, BGP-MD5, integration token).** Reference-only. There is no inline form, because inline is exactly the plaintext-in-spec anti-pattern this removes. If a required reference is absent, the chart renders `fail`, so the HelmRelease — and therefore the app CR `Ready` condition — goes not-Ready with a clear message; it never renders an empty literal. See §4 for two-phase-bootstrap timing and Failure and edge cases for the missing-Secret-after-reference case.

  ```yaml
  site:
    ipsec:
      pskSecretRef:
        name: peer-frankfurt-psk
        key:  psk
  ```

  Because some operators take a name-only reference with a fixed key layout (class 2 in Context), each engine's schema documents the required Secret shape — `{name, key}` where the operator accepts a key selector, or `name` plus a mandated key set (e.g. `username`+`password`, or a `kubernetes.io/basic-auth`-typed Secret) where it does not.

### 2. Rendering: reference-passing, never content-copy

The chart resolves the choice into the operator's existing native reference field. It never reads the referenced Secret at render time (`lookup` is Flux-invisible, §Context) and never copies its content into a chart-owned Secret (`valuesFrom` is pinned, §Context). The value reaches the workload only at runtime, through the operator or a pod `secretKeyRef`, where rotation and late creation are observed natively.

Concretely, per credential the chart computes `(secretName, secretKey)`:

- reference set → `secretName, secretKey = ref.name, ref.key` (the tenant's Secret);
- inline set → the chart materialises `<release>-credentials` as today and uses `<release>-credentials, <field>`;
- neither, generatable → auto-generate into `<release>-credentials` as today, use that;
- neither, required-external → render `fail`, so the HelmRelease and the app CR `Ready` go not-Ready with a clear message.

then fills the operator CR's native field with `{secretName, secretKey}`. This is exactly what `kubernetes` OIDC does at `cluster.yaml:318-323`, generalised.

### 3. The `cozy-lib` helper (the unified deliverable)

`cozy-lib` has no credential helper today (`packages/library/cozy-lib/templates/` — only `tls.caCertSecret` is secret-adjacent). Two helpers move the duplicated logic into the library:

- `cozy-lib.credentials.secret` — the idempotent generate-or-reuse renderer: given `(namespace, secretName, dict-of-field→inlineValueOrEmpty, $)`, it `lookup`s the existing Secret, reuses present keys, generates (`randAlphaNum`) the missing ones, and renders the `<release>-credentials` Secret. This is the eight-chart inline idiom, factored once. It fails closed via `cozy-lib.checkInput` when `$` is not passed, like every other helper.
- `cozy-lib.credentials.ref` — the resolver from §2: given `(inlineValue, secretKeyRef, defaultName, defaultKey)`, returns the reference to splice into the operator CR — a `{name, key}` map for key-selectable operators (class 1), or a bare `name` for name-only operators (class 2), for which the helper also validates that the caller declared the operator's required key layout. Renders nothing; pure selection.

An engine chart then reads: call `cozy-lib.credentials.ref` per credential, drop the result into the operator's `secretKeyRef`/`importCredentialsSecret`/`auth.secretPath` field, and call `cozy-lib.credentials.secret` once for the inline/generated fields. No chart re-implements generation or reference selection.

### 4. Two-phase bootstrap and RBAC

The referenced Secret lives in the application's own (tenant) namespace and is created by the tenant **before** the reference resolves. The credential value is read by the operator/pod at runtime from that namespace — the same-namespace `secretKeyRef` the operators already use — so no cross-namespace RBAC is introduced and the chart adds nothing to `spec.secrets` (the Secret is a tenant-owned input, not a chart-owned tenant-projected output; §Context, lineage webhook).

The open dependency is the tenant's ability to **create** that Secret. Tenant ServiceAccounts today get `get/list/watch` on `core.cozystack.io/tenantsecrets` and no verbs on raw `core/v1` Secrets (`clusterroles.yaml:45-51`). Whether the tenant `use`/`admin` tier can create a Secret in its namespace, or whether the write must go through a TenantSecret create path, is unresolved and tracked in Open Questions — it is a prerequisite for this contract to be usable end-to-end.

## User-facing changes

- A new optional `*SecretRef` / `valueFrom.secretKeyRef` field on credential-bearing app specs (per engine, as each is converted). The dynamic-API openAPISchema and dashboard form pick it up automatically via `make generate` (`cozyvalues-gen` → `values.schema.json` → `hack/update-crd.sh` → ApplicationDefinition `openAPISchema`).
- `kubectl get <app> -o yaml` shows `{name, key}` instead of a credential when the reference form is used.
- Dashboard: a "reference an existing secret" affordance alongside the inline field; no change for tenants who keep using auto-generation.
- Docs: a single "supplying credentials by reference" page the per-engine docs link to.

## Upgrade and rollback compatibility

- **Additive.** The new field is optional and defaulted empty. Existing CRs — inline password or auto-generated — render byte-identically; the resolver's "neither set" branch is today's behaviour. No migration script, no `migrations.targetVersion` bump.
- **No `Secret.type` change, no HR split, no CRD-required-field addition** — none of the upgrade hazards in the review checklist apply, because nothing existing is renamed, moved, or made mandatory.
- **Rollback.** Reverting a chart to a pre-contract version drops the `*SecretRef` field from the schema; a CR still carrying it is rejected or ignored on the next apply (exact behaviour depends on the dynamic API's unknown-field handling — confirm per engine). For an adopter that then falls back to auto-generation, the chart regenerates a fresh password into `<release>-credentials`, **changing the live credential** — so rolling back an adopted engine is a credential-rotation event, not a no-op. Rollback is clean only for clusters that never adopted the field.
- **`make generate` artifacts** (`values.schema.json`, `README.md`, ApplicationDefinition `openAPISchema`) must be regenerated and committed in each conversion PR, or the dashboard rejects the new field (stale-schema trap).

## Security

- **Primary win: the credential leaves the CR spec.** Today the value is readable by every tenant subject that can `get` the app CR — and the default tenant role `cozy:tenant:base` already grants `apps.cozystack.io` `*`/`*` (`packages/system/cozystack-basics/templates/clusterroles.yaml:33-35`), so that is effectively every tenant subject, not merely an elevated tier. Moving the value out of the spec closes that leak class entirely.
- **Trust boundary of the referenced Secret.** It stays a same-namespace, tenant-owned Secret read at runtime by the operator/pod. It is deliberately **not** labelled `internal.cozystack.io/tenantresource` and **not** added to `spec.secrets`, so it is not projected outward as a TenantSecret — a private input must not become a tenant-visible output. Reviewers should confirm no conversion PR adds the referenced Secret to `spec.secrets`.
- **No new cross-namespace read.** No operator gains access to a namespace it could not already read; the reference is same-namespace.
- **Inline stays available but discouraged.** Because the inline field remains valid, a tenant can still leak by choosing to inline. The contract makes the safe path available and idiomatic; enforcement (removing inline) is the password-cleanup work's decision, not this proposal's.

## Failure and edge cases

- Both inline and `secretRef` set → render `fail` with a clear message (OIDC precedent).
- Required external `secretRef` absent → render `fail` (HR and app `Ready` go not-Ready). The chart never generates a substitute for a required external secret and never renders an empty literal.
- `secretRef` set but names a Secret that does not exist yet → the chart cannot detect this (it does not `lookup` the referenced Secret — that is Flux-invisible, §Context). It always emits the reference; the absence surfaces at runtime on the consumer. For an operator-CR consumer (mariadb, rabbitmq, redis, CNPG managed roles) the error lands on the operator CR's own status, and the app CR still reads `Ready` from its HelmRelease — the app-level signal is then the `WorkloadsReady` enrichment (`pkg/registry/apps/application/rest.go:1377-1435`), which reflects it only once the operator-created workload is observed unhealthy. For a user-scoped credential (a rabbitmq `User`, a CNPG managed role) the cluster workload can stay healthy while only the user object is stuck, so the problem may never surface at the app level and shows only on the operator CR. For a consumer the chart renders directly (a Deployment/StatefulSet env or volume), the Helm wait times out and the HR goes not-Ready. Each engine's conversion states which case applies.
- `secretRef.key` missing from an existing Secret → same as above: a runtime error on the operator or the workload, not a render-time one (the chart does not read the Secret).
- Secret rotated after the app is running → a pod that mounts the Secret as a volume sees the projection update in place; `env`-var `secretKeyRef` and SQL-applied passwords (postgres) are fixed at container start / apply time and do **not** rotate live. The chart is not involved either way; each engine documents whether a credential change needs a restart or a re-apply.
- Reference to a Secret in another namespace → rejected: the type is same-namespace by definition; cross-namespace is out of scope.

## Testing

- **cozy-lib unit tests** (`packages/tests/cozy-lib-tests/`, alongside the existing `tls_cacert_test.yaml`): `credentials.ref` selection matrix (inline-only, ref-only, neither, both→fail) and `credentials.secret` idempotency (existing keys reused, missing generated, `checkInput` guard).
- **Per-engine `helm unittest`** on the first converted chart (`mariadb`, class 1): assert `passwordSecretKeyRef` points at the tenant Secret when the ref is set and at `<release>-credentials` otherwise; assert the referenced value never appears in the rendered CR spec.
- **e2e** (`hack/e2e-apps/*.bats`): create a Secret, reference it from a `mariadb`, assert the engine authenticates with it and that `get mariadb <n> -o yaml` contains no credential value. Poll the durable outcome (successful auth / engine Ready), not a hook resource.

## Rollout

1. **This proposal** — accept the contract.
2. **`cozy-lib` helper + tests** — one PR adding `credentials.ref` / `credentials.secret` and unit tests. No chart behaviour change yet.
3. **First engine: `mariadb`** (class 1 — native `passwordSecretKeyRef`) — convert `users[].password` to inline-or-ref using the helper; regenerate schema/RD; e2e. Reference implementation the class-1/2 engines copy.
4. **Class 1 / 2 engines** — `mongodb` (key-selectable), then `rabbitmq` / `redis` (name-only, mandated key layout), one PR each. `redis` still needs the ref path even though its generation is already correct.
5. **Class 3 engines** — `postgres` and `clickhouse` require adopting a new operator mechanism first (CNPG `managed.roles[].passwordSecret`; an Altinity secret-backed password setting), so they are sequenced last and scoped as their own design + implementation, not a copy of the reference PR.
6. **External-secret consumers** — `cozystack/community#30` `site-gateway` uses the reference-only form for IPsec PSK / BGP-MD5 from day one; no inline path is ever added there.
7. **Plaintext deprecation** — owned by the engine password-cleanup work, sequenced after the engines are converted; out of scope here.

## Open questions

1. **Tenant write path for the referenced Secret.** Can a `use`/`admin`-tier tenant create a `core/v1` Secret in its namespace today, or must the two-phase bootstrap go through a TenantSecret *create* path (which `pkg/registry/core/tenantsecret` does not currently expose for write)? This is the one hard prerequisite; the contract is unusable end-to-end without it. Needs a decision with the API owners.
2. **Field naming.** `passwordSecretRef: {name, key}` (flat, one per credential) vs. the full Kubernetes `valueFrom: {secretKeyRef: {name, key}}` envelope (verbose but instantly familiar). Leaning flat for the `users` map; input wanted.
3. **Should the helper also stamp a rotation-observability annotation** on the operator CR so engines that need a restart-on-change get one uniformly, or is that per-engine? (Interacts with the password-cleanup work's rotation mechanism — do not duplicate.)

## Alternatives considered

- **Inject the Secret content into chart values via a second `valuesFrom`.** Rejected: `expectedValuesFrom()` hardcodes the single `cozystack-values` reference and overwrites drift (`applicationdefinition_helmreconciler.go:99-107`). Not extensible without changing the reconciler, and it would still route the value through the values layer.
- **Resolve the reference at render time with `lookup` and re-render the chart.** Rejected: `lookup` is invisible to the Flux digest, so the chart would not re-render on rotation or late Secret creation, and it would need a manual `helm upgrade` to pick up the value — the exact trap `unified-tls-pki` documents.
- **Chart copies the referenced Secret into a chart-owned `<release>-credentials`.** Rejected: duplicates the secret material, desynchronises on rotation, and re-introduces a chart-owned copy of a value the tenant already owns. The OIDC precedent deliberately mounts the tenant Secret without copying (`cluster.yaml:314-316`).
- **A new CRD / aggregated resource for credentials.** Rejected as over-engineering (proposal non-goal): the operators already accept `{name, key}` references and the platform already has a tenant-Secret channel; a new kind adds RBAC and controller surface for no capability the reference field lacks.
- **Do nothing, keep inline + rely on RBAC.** Rejected: Kubernetes RBAC cannot scope `get <app>` to hide a single spec field, so any subject that can read the CR reads the credential. The value must not be in the spec in the first place.

---

<!-- Inspired by KubeVirt enhancement proposals and Kubernetes Enhancement Proposals (KEPs). -->
