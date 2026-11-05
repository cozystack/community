# Tenant-supplied backup destination and options

- **Title:** `Tenant-supplied backup destination and driver options`
- **Author(s):** `@androndo`
- **Date:** `2026-09-22`
- **Status:** Draft

## Overview

The backup side of the `backups.cozystack.io` API is *admin chooses the configuration, tenant picks a class*: a tenant references a cluster-scoped `BackupClass` and cannot express where a backup goes or what it should contain. The restore side already accepts tenant driver-specific input (`RestoreJobSpec.Options`), but the backup side has no equivalent.

This proposal adds two tenant-writable fields to `Plan` and `BackupJob`: a `storageRef` naming a storage object the tenant owns (a reference, not an inline struct, so each storage backend is its own kind and core stays out of storage semantics), and an opaque `options` blob (`*runtime.RawExtension`, symmetric to `RestoreJobSpec.Options`) for driver-specific backup scope and mode. The driver records the storage actually used on `Backup.status`, where restore and cleanup read it. Core does not interpret `options`; it carries `storageRef` from `Plan` to `BackupJob` to `Backup` and passes both through to the strategy driver.

## Scope and related proposals

- **cozystack/cozystack#4235** (the S3 `Bucket` backup driver) motivates this. A bucket copy that lands in the platform's own S3 shares the source's failure domain; the destination that separates that failure domain is a storage target named outside the source. That PR ships the driver with `Kind: Bucket` left **unbound** in `cozy-default` (following the `cozy-default-foundationdb` precedent); binding it into a default class becomes meaningful only once a storage target is expressible — i.e. after this proposal lands.
- **community#82** (tenant-supplied secrets) is a hard dependency for the external S3 storage kind's credentials; see Credentials.
- **community#77** (backup retention) consumes `Backup.storageRef` as the stable place its cleanup looks for artifacts.

## Decisions

Settled in the review thread on this PR (with @lllamnyp); folded into the Design below:

- **Reference, not inline struct.** The destination is a `storageRef` to a storage object whose kind owns its own schema, rather than an inline S3 struct in the core types. Keeps core out of storage semantics (a new backend is a new kind, not a core API change), lets validation and a reachability probe run once against a `Ready` object, and gives restore/cleanup (incl. community#77) a live anchor.
- **Credentials via community#82**, not a tenant write grant on `TenantSecret`. The earlier write-only `TenantSecret` revision is withdrawn.
- **Per-strategy opt-in, integrity-gated.** A strategy declares which storage kinds it supports and whether its restore is integrity-checked; single-artifact strategies verify `Backup.status.artifact.checksum` before restoring (fail-closed on tampering), and strategies whose restore applies privileged input (Velero) stay off tenant-writable storage until restore validates what it applies.
- **Tenant-owned storage is failure-domain separation, not a platform durability SLA.** The platform cannot enforce retention or immutability on storage it does not control.

## Context

Relevant types in [`api/backups/v1alpha1`](https://github.com/cozystack/cozystack/tree/main/api/backups/v1alpha1):

- `RestoreJobSpec.Options *runtime.RawExtension` — "a driver-specific blob of restore options". The restore path already accepts tenant driver-specific input.
- `PlanSpec` (`ApplicationRef`, `BackupClassName`, `Schedule`) and `BackupJobSpec` (`PlanRef`, `ApplicationRef`, `BackupClassName`) — no place for tenant input, no destination.
- `Backup.status.artifact` (`uri`, `sizeBytes`, `checksum`) — the driver already records a per-artifact checksum field here; it is in-cluster, platform-controlled, and outside any tenant-writable store. Unused today.
- `BackupClassStrategy.Parameters map[string]string` — admin-owned, on the cluster-scoped `BackupClass` (read-only to tenants). Persisted verbatim into `Backup.status.underlyingResources` (tenant-readable), and therefore documented to **never carry credentials**.

### The problem

Three tenant needs have no home today:

1. **Tenant-supplied destination.** A tenant cannot name a storage target it owns to separate a backup's failure domain from its source — the gap that keeps the `Bucket` driver unbound.
2. **Selective / partial backup.** Only specific Kafka topics, DB tables, or object prefixes — a per-run/per-plan decision, currently fixed admin-side.
3. **Backup mode.** Full vs incremental and similar per-plan driver knobs.

## Goals

- A tenant can set, per `Plan` or ad-hoc `BackupJob`, a storage target it owns and driver-specific backup options, without a Strategy or BackupClass change.
- No credential is ever inlined into a spec, status, or audit record; a tenant never writes a core `v1/Secret` in its namespace.
- The default (`cozy-default`) flow is unchanged for tenants who set neither field.
- The mechanism is generic: core does not learn per-driver or per-backend storage semantics.

### Non-goals

- Core interpreting `options` (drivers do) or the storage kinds' schemas (each kind does).
- Mandating off-platform backups; this makes a tenant storage target *expressible*.
- Changing `BackupClassStrategy.Parameters` (it stays admin-owned, as-is).
- Tenant-writable off-platform storage for strategies whose restore applies privileged input (Velero/VM): that needs restore-side validation first and is a separate track (see Open questions).

## Design

### 1. Storage reference

```go
// PlanSpec / BackupJobSpec (added field)

// StorageRef optionally names where backups are written, overriding the storage
// configured by the resolved BackupClass/Strategy. The referenced object's kind
// decides what the destination means. When omitted, the BackupClass/Strategy
// storage applies (e.g. the platform cozy-backups bucket) — the default is
// unchanged.
// +optional
StorageRef *corev1.TypedLocalObjectReference `json:"storageRef,omitempty"`
```

```go
// BackupSpec (added field): the storage actually used, recorded by the driver
// and immutable afterwards. Restore and cleanup resolve storage from here.
StorageRef corev1.TypedLocalObjectReference `json:"storageRef"`
```

Two kinds cover the motivating cases; both are namespaced and tenant-owned:

1. **`apps.cozystack.io/Bucket`** — the tenant's own Bucket app. Coordinates and credentials come from the COSI `BucketAccess` the platform already provisions, so the tenant supplies no secret at all. No dependency on community#82.
2. **`backups.cozystack.io/S3Storage`** — an external S3 target holding `endpoint`, `bucket`, `region`, `prefix`, optional CA, and a credentials reference resolved through community#82 (an entry in the tenant's own secret store, materialised via a chart-rendered `ExternalSecret` into a Secret the tenant can't read). All non-credential fields are server-validatable, and the object carries a `Ready` condition set by validating endpoint policy and a reachability probe once, rather than at every run.

**Per-strategy support + integrity.** A strategy declares the storage kinds it supports and whether its restore is integrity-checked. Admission rejects a `Plan` whose strategy cannot honor the referenced kind — "the driver cannot honor the destination" becomes an admission error, not a failed run. For integrity-checked strategies the driver records the snapshot checksum on `Backup.status.artifact.checksum` at backup time (hash-on-write, over the bytes it streams out — not re-read from the tenant-writable store), and restore verifies it before applying, so a tampered artifact is fail-closed rather than an attacker-controlled apply.

### 2. Opaque driver options (symmetric to restore)

```go
// PlanSpec / BackupJobSpec (added field)

// Options is a driver-specific blob of backup options — selective scope (e.g.
// Kafka topics, DB tables, object prefixes) and mode (e.g. full vs incremental).
// Typed and validated by the strategy driver selected via BackupClassName +
// ApplicationRef; opaque to core. Symmetric to RestoreJobSpec.Options.
//
// The destination does NOT belong here — it is the typed StorageRef. Options
// carries no secret material; like Parameters it is persisted into the
// (tenant-readable) Backup artifact, so each driver validates its own options.
// +optional
// +kubebuilder:pruning:PreserveUnknownFields
Options *runtime.RawExtension `json:"options,omitempty"`
```

`RawExtension` matches `RestoreJobSpec.Options` exactly: a driver defines one Go type and unmarshals both backup and restore options into it, with real types (`Incremental bool`, `Topics []string`) instead of string parsing. The usual `RawExtension` cost — apiserver cannot validate or prune it — is acceptable here precisely because the destination is a separate, referenced, validatable object; what remains in `options` is scope/mode.

### 3. Flow

1. A tenant sets `storageRef` and/or `options` on a `Plan` (every run) or an ad-hoc `BackupJob` (single run). `Plan` values are snapshotted onto each `BackupJob` the plan creates.
2. The `BackupJob` controller resolves `BackupClassName` + `ApplicationRef` to a strategy, admission having confirmed the strategy supports the referenced storage kind, and renders it with the existing context (`Application` + admin `Parameters`) plus the resolved storage (the driver reads the kind's coordinates and credentials) and `options` (unmarshalled by the driver).
3. The driver records the storage actually used on `Backup.spec.storageRef` (immutable) and the artifact checksum on `Backup.status.artifact.checksum`, so restore and cleanup resolve the target from a live object and verify integrity. `options` round-trips into `Backup.status.underlyingResources`.

## User-facing changes

- Tenants gain `spec.storageRef` and `spec.options` on `Plan` and `BackupJob`, plus the namespaced `S3Storage` kind (and the existing `Bucket` app) as destinations.
- Destination credentials follow community#82 (`Bucket` needs none); tenants never write a core `v1/Secret`.
- No change for tenants who set neither field. Dashboard/forms can expose the fields per driver once available.

## Upgrade and rollback compatibility

- Additive, optional `storageRef`/`options`; existing `Plan`/`BackupJob` objects are unaffected, and the `cozy-default` flow is unchanged when both are empty.
- Drivers that read neither field behave exactly as today; a driver adopts the fields independently, declaring its supported storage kinds as it does.
- Rollback: removing the fields reverts to admin-only destinations; backups taken with a tenant storage target remain restorable as long as the referenced storage object still exists and the driver still reads `Backup.spec.storageRef`.

## Security

- **No inlined credentials.** `storageRef` is a reference; the `S3Storage` kind holds only non-secret coordinates plus a community#82 credentials reference, and `Bucket` holds none (COSI `BucketAccess`). `options` carries no secrets. This preserves the invariant `BackupClassStrategy.Parameters` already documents.
- **Tamper resistance.** For integrity-checked strategies restore verifies `Backup.status.artifact.checksum` (recorded in-cluster, outside the tenant-writable store) before applying, so a tenant overwriting an artifact in its own store yields a fail-closed restore, not an attacker-controlled apply. Strategies whose restore applies privileged input (Velero) are kept off tenant-writable storage under the per-strategy opt-in.
- **SSRF / egress.** A tenant-named endpoint reached from a writer with platform-wide privileges is an SSRF surface, and error text in `status.message` is a response channel. The `S3Storage` kind's `Ready` probe and admission enforce an admin-owned endpoint policy (allowed schemes, no private or link-local ranges), plus egress limits wherever the writer runs outside the tenant namespace. Drivers whose writer runs in the application namespace (Bucket's `s3-mirror` Job) use the tenant's own egress position.
- **Isolation.** `storageRef` is namespaced; a tenant can only reference storage objects and Secrets in its own namespace, and the target it names is its own.
- **Not a durability SLA.** On storage the platform does not control, the tenant can delete artifacts, disable immutability, or rotate credentials; the guarantee is failure-domain separation, not platform-enforced retention.

## Failure and edge cases

- Strategy does not support the referenced storage kind → **admission rejects the `Plan`/`BackupJob`**, before any run.
- Driver cannot honor a supported destination at run time → the run is **rejected** (marked failed), never silently falling back to the class default.
- A referenced storage object's spec is edited after a backup is taken → see Open questions (the destination-defining fields should be immutable while referenced, or the coordinates snapshotted onto `Backup`).
- Tenant deletes the artifact in its own store → restore **fails** (availability), never a silent wrong-apply.
- Restore artifact fails checksum verification → **rejected**, not applied.
- Unknown `options` keys → driver ignores or rejects (driver's choice); core does not interpret them.

## Testing

- Unit: spec validation (`storageRef` kind allowed for the strategy; no inlined creds on `S3Storage`), strategy render with the resolved storage + `options` over admin `Parameters`, checksum recorded on backup and verified on restore (tamper → fail-closed), round-trip of `storageRef`/`options` into and back out of the Backup.
- e2e: a tenant-owned `S3Storage` (or `Bucket`) + `Plan.storageRef` → backup lands in the tenant target, restore reads it back; a strategy that does not support the kind is rejected at admission; a tampered artifact fails restore.

## Rollout

1. Land `storageRef`/`options`, the `S3Storage` kind, the per-strategy support declaration, and validation in the core API (no driver behavior change yet).
2. Bucket driver adopts `storageRef` first (COSI `BucketAccess`, no community#82 dependency), with restore checksum verification — this is the motivating case and unblocks #4235.
3. `S3Storage` credentials land with community#82; scope/mode `options` for Kafka/ClickHouse/Postgres follow.
4. Only then consider binding `Kind: Bucket` into `cozy-default` against a tenant-required destination.

## Open questions

- **Storage-object mutability vs existing backups.** `Backup.storageRef` points at a live object; if that object's destination-defining fields (endpoint/bucket/prefix/creds) are edited after a backup is taken, existing Backups resolve to different coordinates and restore breaks. A finalizer blocks *deleting* a referenced storage object but not *editing* it. Make those fields immutable while referenced (CEL), or snapshot the resolved coordinates onto `Backup.status`?
- **What durability the class default guarantees.** Two distinct stories, only one needing the external kind: immutability (Object Lock/WORM) plus a separate failure domain on the platform's own backup store, versus a tenant-supplied off-platform target. The `Bucket` default should state which it relies on, so it does not imply off-platform durability where only immutability is in play.
- **How a strategy declares supported storage kinds** and whether its restore is integrity-checked: a field on the strategy CR, or an admission-side table.
- **Enforcement mechanism** for the kind/credentials rules (a referenced `S3Storage` credentials reference resolves through community#82; no key material inlined): CEL on the CRDs vs an admission webhook.
- **Off-platform durability for privileged-apply strategies (Velero/VM)**, if a later requirement calls for it: it cannot ride this opt-in, because restore there applies tenant-controlled manifests with the restore controller's privileges; it needs restore-side validation (signed backups / verified-apply) first. A separate track, not a switch to flip later.
- Whether `RestoreJobSpec.Options` and the new backup `options` should share a documented per-driver schema convention (a shared struct per driver is the intent).

## Alternatives considered

- **Inline `destination` struct in the core types.** An earlier revision of this proposal put S3 coordinates (endpoint/bucket/prefix/credentialsRef) directly on `Plan`/`BackupJob`. Rejected: it pulls the S3 schema into the core `backups.cozystack.io` types, makes every new backend a core API change, and validates the same coordinates on every Plan and BackupJob rather than once on a `Ready` object. A `storageRef` to a typed kind keeps core out of storage semantics (as `strategyRef`/`BackupClass` keep it out of backup mechanics) and gives restore/cleanup a live anchor.
- **Tenant write access to `TenantSecret` for credentials.** Rejected in favor of community#82: tenants keep secrets in their own store and reference an entry, with no grant on Secrets at all.
- **`options map[string]string` instead of `RawExtension`.** Simpler and merges trivially with `Parameters`, but loses symmetry with `RestoreJobSpec.Options` and forces string-encoding of lists/bools. Rejected in favor of matching the restore side and letting drivers use real types.
- **Destination inside `options` (`RawExtension`).** Rejected: the destination is durability-critical and secret-adjacent; a typed, referenced object is where validation of the secret/non-secret split belongs, not an unvalidatable blob.
- **Fall back to the class default when a driver cannot honor a destination.** Rejected: it would silently drop the tenant's intent. The run is rejected instead.
