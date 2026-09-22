# Tenant-supplied backup destination and options

- **Title:** `Tenant-supplied backup destination and driver options`
- **Author(s):** `@androndo`
- **Date:** `2026-09-22`
- **Status:** Draft

## Overview

The backup side of the `backups.cozystack.io` API is *admin chooses the
configuration, tenant picks a class*: a tenant references a cluster-scoped
`BackupClass` and cannot express where a backup goes or what it should contain.
The restore side already accepts tenant driver-specific input
(`RestoreJobSpec.Options`), but the backup side has no equivalent.

This proposal adds two tenant-writable fields to `Plan` and `BackupJob`: a typed
`destination` (a target the tenant owns, with credentials referenced through a
`TenantSecret`), and an opaque `options` blob (`*runtime.RawExtension`, symmetric
to `RestoreJobSpec.Options`) for driver-specific backup scope and mode. Core does
not interpret `options`; it validates `destination` structurally and passes both
through to the strategy driver.

## Scope and related proposals

- **cozystack/cozystack#4235** (the S3 `Bucket` backup driver) motivates this. A
  bucket copy that lands in the platform's own S3 shares the source's failure
  domain and provides no durability; the destination that makes it meaningful is
  one the tenant owns elsewhere, which the API cannot name today. That PR ships
  the driver with `Kind: Bucket` left **unbound** in `cozy-default` (following the
  `cozy-default-foundationdb` precedent); binding it into a default class becomes
  meaningful only once a tenant destination is expressible — i.e. after this
  proposal lands.

## Decisions

<!-- Filled in as implementation proceeds; records live under ./decisions/. -->

## Context

Relevant types in [`api/backups/v1alpha1`](https://github.com/cozystack/cozystack/tree/main/api/backups/v1alpha1):

- `RestoreJobSpec.Options *runtime.RawExtension` — "a driver-specific blob of
  restore options". The restore path already accepts tenant driver-specific input.
- `PlanSpec` (`ApplicationRef`, `BackupClassName`, `Schedule`) and `BackupJobSpec`
  (`PlanRef`, `ApplicationRef`, `BackupClassName`) — no place for tenant input, no
  destination.
- `BackupClassStrategy.Parameters map[string]string` — admin-owned, on the
  cluster-scoped `BackupClass` (read-only to tenants). Persisted verbatim into
  `Backup.status.underlyingResources` (tenant-readable, replicated through every
  Backup artifact), and therefore documented to **never carry credentials**.
- [`pkg/registry/core/tenantsecret/rest.go`](https://github.com/cozystack/cozystack/blob/main/pkg/registry/core/tenantsecret/rest.go)
  — `TenantSecret` is already a full `Creater`/`Updater`/`GracefulDeleter` that
  guards the platform-owned `internal.cozystack.io/` label/annotation namespace
  against untrusted writers. Tenant tiers grant `get`/`list`/`watch` on
  `tenantsecrets` but no write verb.

### The problem

Three tenant needs have no home today:

1. **Tenant-supplied destination.** A tenant cannot name a bucket, endpoint, or
   credential to send a backup to a target it owns off-platform — the only place a
   bucket backup earns durability.
2. **Selective / partial backup.** Only specific Kafka topics, DB tables, or
   object prefixes — a per-run/per-plan decision, currently fixed admin-side.
3. **Backup mode.** Full vs incremental and similar per-plan driver knobs.

## Goals

- A tenant can set, per `Plan` or ad-hoc `BackupJob`, a destination it owns and
  driver-specific backup options, without a Strategy or BackupClass change.
- No credential is ever inlined into a spec, status, or audit record; a tenant
  never writes a core `v1/Secret` in its namespace.
- The default (`cozy-default`) flow is unchanged for tenants who set neither field.
- The mechanism is generic: core does not learn per-driver semantics.

### Non-goals

- Core interpreting `options` (drivers do).
- Non-S3 destinations (every current driver mirrors to S3).
- Mandating off-platform backups; this makes a tenant destination *expressible*.
- Changing `BackupClassStrategy.Parameters` (it stays admin-owned, as-is).

## Design

### 1. Typed destination

```go
// BackupDestination names where a backup is written when a tenant overrides the
// strategy/BackupClass default with a target it owns. All fields are non-secret:
// credentials are referenced through a TenantSecret, never inlined, so the
// destination can be persisted into the (tenant-readable) Backup artifact.
type BackupDestination struct {
	// Endpoint is the S3(-compatible) endpoint URL, scheme included.
	Endpoint string `json:"endpoint"`

	// Bucket is the destination bucket name.
	Bucket string `json:"bucket"`

	// Region is the S3 region, when the endpoint requires one.
	// +optional
	Region string `json:"region,omitempty"`

	// Prefix is an optional key prefix under which objects are written.
	// +optional
	Prefix string `json:"prefix,omitempty"`

	// CredentialsSecretRef references a tenant-owned TenantSecret holding the
	// destination access credentials. It MUST be a TenantSecret (not an
	// arbitrary namespace Secret); its value is never inlined.
	CredentialsSecretRef corev1.LocalObjectReference `json:"credentialsSecretRef"`

	// TLS optionally points at a CA (Secret reference) and/or opts out of
	// verification for a private endpoint.
	// +optional
	TLS *BackupDestinationTLS `json:"tls,omitempty"`
}
```

Added to both specs:

```go
// PlanSpec / BackupJobSpec (added field)

// Destination optionally overrides where backups are written with a tenant-owned
// target. When omitted, the destination configured by the resolved
// BackupClass/Strategy applies (e.g. the platform cozy-backups bucket).
// +optional
Destination *BackupDestination `json:"destination,omitempty"`
```

Every field is non-secret and the only credential is a reference, so `destination`
is fully server-validatable (required `endpoint`/`bucket`/`credentialsSecretRef`,
optional `region`/`prefix`/`tls`) — including a rule that `credentialsSecretRef`
names a `TenantSecret` and that nothing key-shaped is inlined.

### 2. Opaque driver options (symmetric to restore)

```go
// PlanSpec / BackupJobSpec (added field)

// Options is a driver-specific blob of backup options — selective scope (e.g.
// Kafka topics, DB tables, object prefixes) and mode (e.g. full vs incremental).
// Typed and validated by the strategy driver selected via BackupClassName +
// ApplicationRef; opaque to core. Symmetric to RestoreJobSpec.Options.
//
// Destination and credentials do NOT belong here — the destination lives in the
// typed Destination field. Options carries no secret material; like Parameters
// it is persisted into the (tenant-readable) Backup artifact.
// +optional
// +kubebuilder:pruning:PreserveUnknownFields
Options *runtime.RawExtension `json:"options,omitempty"`
```

`RawExtension` matches `RestoreJobSpec.Options` exactly: a driver defines one Go
type and unmarshals both backup and restore options into it, with real types
(`Incremental bool`, `Topics []string`) instead of string parsing. The usual
`RawExtension` cost — apiserver cannot validate or prune it — is acceptable here
precisely because the security-sensitive part (the destination) is a separate,
typed, validatable field; what remains in `options` is scope/mode.

### 3. Flow

1. A tenant sets `destination` and/or `options` on a `Plan` (every run) or an
   ad-hoc `BackupJob` (single run). `Plan` values are snapshotted onto each
   `BackupJob` the plan creates.
2. The `BackupJob` controller resolves `BackupClassName` + `ApplicationRef` to a
   strategy and renders it with the existing context (`Application` + admin
   `Parameters`) plus the tenant `destination` (replacing the strategy's default
   target; the driver reads `CredentialsSecretRef` from the tenant namespace) and
   `options` (unmarshalled by the driver).
3. Non-secret `destination` fields and `options` round-trip into
   `Backup.status.underlyingResources`, so restore/cleanup reproduce the target
   and shape. Only the `credentialsSecretRef` *name* is persisted, never a value.

## User-facing changes

- Tenants gain `spec.destination` and `spec.options` on `Plan` and `BackupJob`.
- Tenants gain write access to `TenantSecret` (RBAC), to store destination
  credentials.
- No change for tenants who set neither field. Dashboard/forms can expose the two
  fields per driver once available.

## Upgrade and rollback compatibility

- Additive, optional fields; existing `Plan`/`BackupJob` objects are unaffected,
  and the `cozy-default` flow is unchanged when both are empty.
- Drivers that read neither field behave exactly as today; a driver adopts the
  fields independently.
- Rollback: removing the fields reverts to admin-only destinations; backups taken
  with a tenant destination remain restorable as long as the driver still reads
  the round-tripped coordinates.

## Security

- **No inlined credentials.** `destination` carries only non-secret coordinates
  plus a `TenantSecret` reference; `options` carries no secrets. This preserves the
  invariant `BackupClassStrategy.Parameters` already documents (its values reach a
  tenant-readable, replicated status field).
- **Tenant credential storage via `TenantSecret`.** The one new trust surface is
  RBAC: granting tenants `create`/`update` on `tenantsecrets`. `TenantSecret`'s
  registry already guards the platform-owned `internal.cozystack.io/` namespace
  against untrusted writers, so this is a policy change, not new machinery. Tenants
  still cannot write a core `v1/Secret` in their namespace.
- **Validation** rejects a `credentialsSecretRef` that does not name a
  `TenantSecret`, and rejects any attempt to inline key material into `destination`.
- **Isolation** is unchanged: a tenant can only reference Secrets and write objects
  in its own namespace; the destination it names is its own.

## Failure and edge cases

- Driver cannot honor a `destination` that is set → the run is **rejected**
  (marked failed), never silently falling back to the class default, so durability
  intent is not lost.
- `credentialsSecretRef` names a missing or non-`TenantSecret` object →
  admission/validation rejects, or the run fails with a clear reason.
- Unknown `options` keys → driver ignores or rejects (driver's choice); core does
  not interpret them.
- Both `destination` and a class default present → tenant `destination` wins.
- Restore of a Backup taken with a tenant destination → reproduces from the
  round-tripped coordinates + the referenced `TenantSecret`.

## Testing

- Unit: spec validation (required fields; `credentialsSecretRef` must be a
  `TenantSecret`; no inlined creds), strategy render with `destination` + `options`
  merged over admin `Parameters`, round-trip into and back out of the Backup
  artifact.
- e2e: a tenant-owned destination Secret + `Plan.destination` → backup lands in the
  tenant target, restore reads it back; a driver that rejects an unsupported
  destination fails the run with the expected reason.

## Rollout

1. Land the `destination`/`options` fields and validation in the core API
   (no driver behavior change yet).
2. Grant tenant write on `TenantSecret`.
3. Drivers adopt the fields one at a time (Bucket first, as the motivating case;
   then scope/mode for Kafka/ClickHouse/Postgres).
4. Only then consider binding `Kind: Bucket` into `cozy-default` against a
   tenant-required destination.

## Open questions

- Enforcement mechanism for "`credentialsSecretRef` must be a `TenantSecret`" and
  "no inlined creds": CEL on the CRD vs an admission webhook.
- Whether `RestoreJobSpec.Options` and the new backup `options` should share a
  documented per-driver schema convention (they need not be identical, but a
  shared struct per driver is the intent).

## Alternatives considered

- **`options map[string]string` instead of `RawExtension`.** Simpler and merges
  trivially with `Parameters`, but loses symmetry with `RestoreJobSpec.Options` and
  forces string-encoding of lists/bools. Rejected in favor of matching the restore
  side and letting drivers use real types.
- **Destination inside `options` (`RawExtension`).** Rejected: the destination is
  the durability-critical, secret-adjacent part, and burying it in an
  unvalidatable blob is exactly where server-side validation of the
  secret/non-secret split is most wanted. Hence a separate typed field.
- **Fall back to the class default when a driver cannot honor a destination.**
  Rejected: it would silently drop the tenant's durability intent. The run is
  rejected instead.
- **Adding `options` to admin `BackupClassStrategy` for defaults.** Rejected:
  drivers already default internally; keeping `options` tenant-only avoids a second
  merge layer.
