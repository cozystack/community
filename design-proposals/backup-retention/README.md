# Configurable backup retention and cleanup

- **Title:** `Configurable backup retention and cleanup`
- **Author(s):** `@androndo`
- **Date:** `2026-09-15`
- **Status:** Draft

## Overview

Cozystack's backup subsystem (`backups.cozystack.io/v1alpha1`: `Plan`, `BackupJob`, `Backup`, `RestoreJob`, `BackupClass`) schedules and records backups but never removes them. Retention is delegated per strategy to the underlying operator, and only CNPG, MariaDB, and Velero expose any knob for it; ClickHouse, MongoDB, etcd, FoundationDB, and Job strategies get no age- or count-based cleanup at all, so their artifacts accumulate in object storage until a tenant deletes them by hand. There is no "keep the last N backups" control anywhere in the API.

This proposal introduces a namespaced CRD, `BackupRetentionPolicy`, carrying three orthogonal knobs — `minCount` (a floor), `maxCount` (a ceiling), and `maxAge` (a TTL) — and makes the platform, not the application operator, the single authority that decides when a `Backup` is removed. A `Backup` joins a policy through one label; that label is stamped from a `retentionPolicyName` field shared by `Plan`, `BackupJob`, and `BackupClass`, the last acting as the namespace default. Enumeration and pruning of `Backup` objects live in a core controller, and physical reclamation of the object-store artifact is delegated to the strategy driver, exactly as the Velero path already turns a `Backup` deletion into a `DeleteBackupRequest`. This closes [cozystack/cozystack#3965](https://github.com/cozystack/cozystack/issues/3965).

## Scope and related proposals

This spans the generic backup framework (the `Plan`/`Backup` API plus its controller) and every per-strategy driver, so it is cross-cutting by design. It builds on the backups core API described in [`api/backups/v1alpha1/DESIGN.md`](https://github.com/cozystack/cozystack/blob/main/api/backups/v1alpha1/DESIGN.md); an API-level sketch of the same design has been added there as §4.6 in [cozystack/cozystack#4192](https://github.com/cozystack/cozystack/pull/4192), which this proposal supersedes as the authoritative document. The controller mechanics borrow from the `EtcdDefragPolicy` pattern in [cozystack/etcd-operator](https://github.com/cozystack/etcd-operator) (a policy object driving an idempotent, deterministic reconcile). Grandfather-father-son (GFS) tiering is deliberately deferred — see Non-goals and Open questions.

## Decisions

<!-- Filled in as implementation proceeds; records live under ./decisions/, newest first. -->

## Context

The core API cleanly separates *when* backups run (`Plan` schedule), *how/where* they are taken (`BackupClass` → strategy), *what artifacts exist* (`Backup`), and *restore* (`RestoreJob`). It does not decide *when artifacts go away*. `DESIGN.md` already earmarks "higher-level policies (retention)" as core's job but leaves it unimplemented, and the MariaDB strategy comment states that when its native `maxRetention` is empty, "tenants rely on Cozystack Plan-level retention only" — a Plan-level retention that does not exist.

What retention exists today, per strategy:

| Strategy | Native retention knob | Deletes data in S3 on `Backup` delete? |
|---|---|---|
| CNPG / Postgres | `retentionPolicy` (Barman expr, e.g. `30d`) | yes (barman) |
| MariaDB | `maxRetention` (duration; empty ⇒ unbounded) | yes (operator) |
| Velero (VM instance / disk) | `ttl` (pass-through of Velero `BackupSpec.TTL`) | yes (`DeleteBackupRequest`) |
| ClickHouse / Altinity | — | no |
| MongoDB | — | no |
| etcd | — | no |
| FoundationDB | — | no |
| Job | — | n/a (owns no surviving artifact) |

The only deletion logic is finalizer-driven cleanup on `Backup` deletion (`internal/backupcontroller/backup_controller.go`), which is a no-op against S3 for every strategy except Velero. There is no age-based pruning loop and no count-based control anywhere.

### The problem

A tenant running an hourly `Plan` against Postgres has no way to say "keep the last 30 backups" — barman only understands a time window, and nothing in Cozystack understands count at all. A tenant backing up ClickHouse or MongoDB gets no cleanup whatsoever: every run adds an artifact to the bucket forever, and deleting the `Backup` object leaves the data behind. An operator who wants one uniform retention rule across a tenant's databases must instead learn three different dialects (`retentionPolicy`, `maxRetention`, `ttl`) that cover only three of the eight strategies.

## Goals

- A declarative, strategy-agnostic retention control covering age (`maxAge`), a count ceiling (`maxCount`), and a count floor (`minCount`), usable independently or in combination.
- Cleanup for strategies whose operators have no native retention (ClickHouse, MongoDB, etcd, FoundationDB), by having core enumerate and prune `Backup` objects and delegating physical deletion to the driver.
- One policy attachable to both scheduled (`Plan`) and ad-hoc (`BackupJob`) backups, with a platform default carried on `BackupClass`.
- The platform, not the application operator, owns the artifact lifecycle end to end.
- Migration of existing artifacts between policies without recreating them.

### Non-goals

- Grandfather-father-son / tiered retention (keep-daily / keep-weekly / keep-monthly) in this iteration — the model is designed to extend to it later (Open questions), but v1 ships flat min/max/age only.
- Point-in-time / WAL-range retention semantics for continuous-archiving engines beyond what `maxAge` expresses.
- Replacing a driver's native retention where the driver, not Cozystack, owns the archive lifecycle and the two would otherwise fight (see Design → cleanup contract).

## Design

### `BackupRetentionPolicy` (new, namespaced)

```go
type BackupRetentionPolicySpec struct {
    // Never delete below this many newest Ready backups; overrides MaxAge. Defaults to 1.
    MinCount int32 `json:"minCount"`
    // Keep at most this many newest Ready backups. Unset means unbounded.
    MaxCount *int32 `json:"maxCount,omitempty"`
    // Delete backups older than this, by spec.takenAt, bounded by MinCount.
    // Suffix h/d/w (e.g. "7d", "90d"); a string, since metav1.Duration has no unit above the hour.
    MaxAge string `json:"maxAge,omitempty"`
}
```

The spec is flat and carries no selector: a `Backup` names exactly one policy in a label — a scalar — so it is never claimed by two policies at once.

### Membership and binding

A `Backup` joins a policy through the `backups.cozystack.io/retention-policy` label, stamped by the core controller when the `Backup` is created. One new optional field, `retentionPolicyName`, feeds it on `Plan`, `BackupJob`, and `BackupClass`, each capped at 63 characters because the value lands in a DNS-1123 label. Resolution when a `Backup` is created, highest precedence first: `BackupJob.spec.retentionPolicyName` (or `Plan.spec.retentionPolicyName`, inherited onto the BackupJob), then `BackupClass.spec.retentionPolicyName`, then none — a `Backup` with no label is never swept.

### Selection algorithm

For a policy `P` in namespace `N`, on every reconcile and on `Backup` create/delete/label events:

1. List `Backup` in `N` labeled `retention-policy=P.name`; keep only `status.phase == Ready`. `Failed`/`Pending` backups never consume the floor and are pruned only by `maxAge`.
2. Sort by `spec.takenAt` descending.
3. Protect the newest `minCount` unconditionally.
4. From the rest, delete a backup when its index `>= maxCount` **or** `now - takenAt > maxAge`. `minCount` always wins over `maxAge`.
5. Delete via the `Backup` object; the `backups.cozystack.io/cleanup` finalizer runs the driver's cleanup path.

The sweep is an idempotent set difference, safe to repeat.

### Namespace-local matching

Both `Backup` and `BackupRetentionPolicy` are namespaced, and matching is scoped to the policy's own namespace. A policy named `default` in one namespace and one so named in another are distinct objects governing disjoint backup sets, so identical names never collide. A name identifies a policy only within a namespace, which suffices because a `Backup` and its policy always share one. The platform default is a policy shipped into each tenant namespace, referenced by `BackupClass.spec.retentionPolicyName`.

### Platform-owned cleanup contract

The platform is the single authority that decides when a `Backup` is removed; it does not delegate expiry to an application operator's TTL or retention window. Deletion is driven through the `Backup` object, and the strategy driver reclaims both the record and the physical archive on the platform's behalf. Every driver that reclaims physically MUST honor:

1. **Single authority.** The operator's native retention (CNPG `Cluster.spec.backup.retentionPolicy`, MariaDB `maxRetention`) is unset, so the operator and the core sweep never race over the same archive.
2. **Idempotency and finalizer-hold.** The `backups.cozystack.io/cleanup` finalizer holds the `Backup` until the driver confirms the archive is gone; a retried or duplicated deletion is a no-op.
3. **Serialization.** Physical deletion for a server MUST NOT run concurrently with an in-flight backup or WAL archiving for that same server.

For a continuous-archiving driver (barman/CNPG) retention is WAL-safe by construction: the sweep only trims the oldest while `minCount` protects the newest, so barman frees only the WALs older than the oldest surviving base. The rules map onto barman directly — `minCount` → `--minimum-redundancy`, `maxAge` → recovery window, `maxCount` → a targeted `backup-delete` of the tail — with the caveat that on such a driver `maxCount` indirectly bounds the recovery window (an hourly `Plan` with `maxCount: 3` collapses PITR to about three hours).

### Prior art

OpenSearch Snapshot Management uses the same three fields (`min_count` / `max_count` / `max_age`, floor overriding age), and this proposal adopts its semantics. CNPG is time-only (a Barman recovery window, no count), which is why a count ceiling needs the driver-side delete path above. `EtcdDefragPolicy` supplies the controller shape: a policy object driving an idempotent, deterministic reconcile.

## User-facing changes

A new namespaced CRD `BackupRetentionPolicy`, and one new optional `retentionPolicyName` field on `Plan`, `BackupJob`, and `BackupClass`. Existing manifests keep working unchanged.

Platform default, shipped per tenant namespace and wired as the BackupClass default:

```yaml
apiVersion: backups.cozystack.io/v1alpha1
kind: BackupRetentionPolicy
metadata:
  name: default
  namespace: tenant-acme
spec:
  minCount: 3
  maxAge: 7d
---
apiVersion: backups.cozystack.io/v1alpha1
kind: BackupClass
metadata:
  name: cozy-default        # cluster-scoped platform BackupClass
spec:
  retentionPolicyName: default
  strategies:
    - application: { apiGroup: apps.cozystack.io, kind: Postgres }
      strategyRef:
        apiGroup: strategy.backups.cozystack.io
        kind: CNPG
        name: cozy-default-cnpg
```

A tenant creating one policy and using it from both a scheduled `Plan` and an ad-hoc `BackupJob`:

```yaml
apiVersion: backups.cozystack.io/v1alpha1
kind: BackupRetentionPolicy
metadata: { name: keep-90d, namespace: tenant-acme }
spec:
  minCount: 5
  maxCount: 60
  maxAge: 90d
---
apiVersion: backups.cozystack.io/v1alpha1
kind: Plan
metadata: { name: pg-src-daily, namespace: tenant-acme }
spec:
  applicationRef: { apiGroup: apps.cozystack.io, kind: Postgres, name: pg-src }
  backupClassName: cozy-default
  schedule: { type: cron, cron: "0 */6 * * *" }
  retentionPolicyName: keep-90d          # overrides BackupClass default
---
apiVersion: backups.cozystack.io/v1alpha1
kind: BackupJob
metadata: { name: pg-src-adhoc, namespace: tenant-acme }
spec:
  applicationRef: { apiGroup: apps.cozystack.io, kind: Postgres, name: pg-src }
  backupClassName: cozy-default
  retentionPolicyName: keep-90d          # same policy for an ad-hoc run
```

Moving artifacts to another policy is a label change, with no `Plan` edit:

```bash
kubectl label backup -n tenant-acme \
  -l backups.cozystack.io/retention-policy=default \
  backups.cozystack.io/retention-policy=keep-90d --overwrite
```

## Upgrade and rollback compatibility

`retentionPolicyName` is optional on all three types, so existing clusters and manifests keep working; a `Backup` with no label is never swept, matching today's "never cleaned" behavior. Adopting platform-owned cleanup for a driver that had native retention (CNPG, MariaDB) requires unsetting that native retention as part of the same change, so the two authorities never overlap. Rollback is removing the policy objects and the field; already-deleted artifacts are not recoverable, so that direction is one-way, but no data is deleted merely by reverting the controller.

## Security

The policy is namespaced and tenant-scoped; matching never crosses a namespace, so one tenant's policy can neither see nor prune another's `Backup`. The policy carries only retention numbers and a name — no credentials, endpoints, or object-store paths. Physical deletion uses the same driver credentials already used to take the backup; no new secret is stored or transmitted. Tenants get CRUD on `BackupRetentionPolicy` in their own namespace via the existing tenant RBAC surface.

## Failure and edge cases

- `retentionPolicyName` points at a non-existent policy → the `Backup` is still taken and labeled; a `RetentionPolicyNotFound` condition is surfaced on the `Plan`/`BackupJob`. A backup is never blocked on a missing retention policy.
- A `Backup` carries no retention label → it is never swept (fail-safe).
- A driver has no physical-reclaim implementation yet → the `Backup` record is removed and the archive is left to the driver's own retention until the driver learns the contract; this is called out per driver rather than silently divergent.
- Relabeling onto a stricter policy → artifacts over the new limits are deleted on the next sweep; onto a laxer one → they are spared.
- `maxCount` on a continuous-archiving driver → collapses the effective recovery window; the effective window is surfaced rather than only the count.

## Testing

Unit tests for the selection algorithm: floor-over-age precedence, count ceiling, age TTL, `Failed`/`Pending` exclusion from the floor. Per-driver e2e that a sweep prunes both the `Backup` object and the object-store artifact, run for at least one operator-native driver (CNPG) and one previously-uncovered driver (ClickHouse or MongoDB). A namespace-isolation e2e that two same-named policies in different namespaces prune disjoint sets.

## Rollout

1. Core `BackupRetentionPolicy` CRD, the `retentionPolicyName` field, and the sweep, enforcing on strategies where Cozystack already owns the archive (Velero, and any Job-to-cozy-bucket driver).
2. Per-driver physical reclamation added incrementally (CNPG barman delete, then ClickHouse / MongoDB / etcd / FoundationDB), each unsetting its native retention as it adopts the contract.
3. GFS/tiered retention as a later extension.

## Open questions

- GFS/tiered shape: additional fields (`keepDaily` / `keepWeekly` / `keepMonthly`) on the same CRD, or a separate policy kind? The flat spec is intended to accept them additively.
- Physical per-backup reclamation for barman/CNPG within the recovery window: run `barman-cloud-backup-delete` from a driver Job, versus keeping `maxCount` record-only on time-only drivers and honoring only `maxAge` physically.
- `maxAge` grammar: settle the accepted suffixes (`h/d/w`) and whether to accept the CNPG-style `m` (months) for parity.

## Alternatives considered

**A field on `Plan.spec` (or `BackupClass`) instead of a standalone CRD.** This is what #3965 first suggested, and it is simpler, but ad-hoc `BackupJob`s that carry no `planRef` fall outside it, several `Plan`s for one application fragment the rule, and a policy cannot be reused or migrated onto existing artifacts. A standalone object keyed by a label covers scheduled and ad-hoc backups uniformly and makes migration a relabel. Rejected in favor of the CRD.

**Selector-based membership** (the policy carries an `applicationRef`/label selector). Flexible, but two selectors can claim one `Backup`, forcing a conflict rule and a fail-safe no-op. A scalar label removes the whole class of conflicts and enables relabel-migration. Rejected.

**A cluster-scoped policy (StorageClass-style).** Global name uniqueness by construction, but tenants could not own a `default`, and a cluster-scoped CRD cannot be granted to tenants for write in a multi-tenant cluster. Rejected.

**Keep delegating per strategy (status quo).** Leaves ClickHouse, MongoDB, etcd, and FoundationDB with no cleanup at all, offers no count-based retention anywhere, and forces users to learn one dialect per engine for the same intent. Rejected.
