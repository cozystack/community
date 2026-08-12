# Platform migration engine

- **Title:** `Platform migration engine: content-addressed IDs, an applied-set ledger, and an authoring contract`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-08-12`
- **Status:** Draft

## Overview

Platform migrations are identified by a dense global integer that also serves as cluster state. Every new migration must claim the next number, which makes concurrent migration PRs conflict by construction; the single scalar recording progress cannot describe a branched history, which makes migrations unbackportable; and the pending set is computed from a number maintained in a different file than the migrations themselves, which makes silent skips possible.

This proposal replaces the integer with a content-addressed ID and an applied-set ledger, splits execution into a blocking tier and a background tier, and puts a machine-checkable contract around migration authoring. Slot contention disappears, backports become safe by construction, and the "migration silently did not run" failure stops being expressible.

## Scope and related proposals

- [`kubernetes-nodes-split`](../kubernetes-nodes-split/) — its Phase 2 migration is used here as a worked example of the blocking tier. No ordering dependency between the two.
- Phase 4 below sketches two mechanisms for reducing the *rate* at which migrations are needed (admission-time defaulting, API-layer conversion). Each is large enough to deserve its own proposal; they are included here only to show where the migration count goes over time.
- Package-scoped migrations (Phase 3) are a prerequisite for carrying migrations out of the monorepo alongside their package, but this proposal does not otherwise depend on decomposition work.

## Context

Migrations live in `packages/core/platform/images/migrations/` and are delivered as a Helm `pre-upgrade,pre-install` hook Job from `packages/core/platform/templates/migration-hook.yaml`, running with `cluster-admin`. All migration scripts are baked into a single image, pinned by digest in `packages/core/platform/values.yaml`.

`run-migrations.sh` walks `seq $CURRENT_VERSION $((TARGET_VERSION - 1))`. `CURRENT_VERSION` comes from a scalar in ConfigMap `cozy-system/cozystack-version`; `TARGET_VERSION` is hand-maintained in `values.yaml`. The chart's `templates/cozystack-version.yaml` creates that ConfigMap on first install, and the hook only renders when `currentVersion < targetVersion`, so a cluster with nothing pending never starts a pod.

There are 53 migrations on disk and `targetVersion` is 54.

### The problem

One integer carries both a migration's identity and the cluster's progress through the sequence. Three consequences follow.

**Slot contention.** Every new migration must claim the next integer, so every pair of concurrent migration PRs conflicts. [#3406](https://github.com/cozystack/cozystack/pull/3406), [#3379](https://github.com/cozystack/cozystack/pull/3379) and [#3315](https://github.com/cozystack/cozystack/pull/3315) all add `migrations/54` at the time of writing. Two must renumber on merge, and renumbering is not a rename — it also means re-checking `targetVersion` and cross-branch alignment.

**Backports are unsafe.** A scalar high-water mark can only express "everything below N is done", which holds only if the sequence is byte-identical on every branch. Branching voids that invariant. [#3534](https://github.com/cozystack/cozystack/issues/3534) is the realised failure: slot 45 holds a different migration on `release-1.5` than on `release-1.6`, so a cluster taking the normal patch-then-minor path is stamped past `release-1.6`'s Kubeadm keep-policy pin and never runs it. Nothing reports this class of divergence; it was found by hand while auditing backports.

**The work list lives in a different file than the migrations.** Nothing derives the pending set from the migrations on disk. `hack/check-migrations-target.sh` exists solely to catch the resulting off-by-one — a lint compensating for a design flaw.

A fourth cost is unbilled. Migration `43` matched the owning Helm release against the literal string `seaweedfs-system`, so it skipped every SeaweedFS instance not named `seaweedfs` — and `SeaweedFS` is a user-creatable kind, so an instance `foo` is owned by release `foo-system`. Those tenants never received `helm.sh/resource-policy: keep`, and the post-split `foo-system` upgrade pruned their `Cluster`. CNPG takes the PVC with the Cluster, so the filer metadata and every object in that tenant's S3 became unreachable. The migration was then edited in place, which fixed clusters that had not yet run it and did nothing for those that had — so migration `53` had to be written to repair them.

## Goals

- Two concurrent migration PRs never contend for the same identifier.
- A migration backported to a maintenance branch cannot cause a forward-upgrade skip.
- The pending set is derived from the migrations on disk, not from a separately maintained number.
- A migration that must run before the chart applies keeps that guarantee; one that does not, stops blocking upgrades.
- Authoring a migration follows a contract CI can check, rather than convention rediscovered per author.
- Obsolete migrations can be deleted rather than shipped forever.

### Non-goals

- Rewriting the 53 existing integer migrations' logic. They are already applied on essentially every cluster; their content is frozen. They are renamed, but only mechanically, in Phase 5.
- Moving migrations out of shell. The operations are `kubectl` calls, and shell keeps them reviewable and testable against fixtures.
- Replacing the `cozystack-version` ConfigMap with a new API kind. It is extended in place.

## Design

### 1. Identity and layout

Identity is `YYYYMMDD-slug`. Tier is the **directory**, so nothing is parsed at render time and there is no generated index to keep in sync:

```
packages/core/platform/images/migrations/migrations/
  1 .. 53            # legacy integer set — frozen, never extended
  lib/
  revoked            # IDs that must not run (§8)
  pre-apply/         # blocking, runs in the pre-upgrade hook Job
    20260812-redis-failover-group-label
  background/        # non-blocking, run by cozystack-operator
    20260814-clickhouse-keeper-pvc-labels
```

### 2. Tiers

The two tiers already exist informally, written into the migration headers. Migrations `48`, `49` and `51` each carry the same sentence: *"Best-effort by design: the worst case if a relabel is skipped is the pre-existing bug this PR set out to fix, which is strictly no worse than the status quo."* That is the background criterion, reasoned about case by case in prose. This proposal makes it declarable and executable.

**`pre-apply` is the default**, and the test is not "is this a backfill" — it is *can something be named that breaks if this runs late?* Chart render guards, operator label selectors and Helm prunes all count:

| migration | what depends on it having already run |
|---|---|
| [#3315](https://github.com/cozystack/cozystack/pull/3315) worker pools | creates the child `kubernetes-nodes-<cluster>-<pool>` HelmReleases before the kubernetes chart stops rendering worker objects. Skip it and Helm prunes the tenant's running worker VMs. |
| [#3379](https://github.com/cozystack/cozystack/pull/3379) FluxCD addon removal | suspends and drops the finalizer on tenant Flux HelmReleases so the delete does not uninstall Flux *inside* the tenant cluster. |
| [#3406](https://github.com/cozystack/cozystack/pull/3406) RedisFailover | stamps the operator-group label and pins the engine image before redis-operator v3.3.5 rolls. An unlabelled CR never reaches the new operator's informer at all — no event, no error. |
| `46` k8s version bump | patches `kuberneteses.apps.cozystack.io` `spec.version` v1.30 → v1.31 **ahead of the chart upgrade**. The chart carries an explicit `fail()` guard on v1.30 in `templates/_versions.tpl`, so a tenant still on v1.30 when the platform upgrade lands gets a failed HelmRelease and the Talos worker rollover never starts. |
| `53` seaweedfs | hands `Cluster/seaweedfs-db` to the `<name>-db` release before `<name>-system` re-renders without it. CNPG takes the PVC with the Cluster. |
| `52` linstor-scheduler | deletes the old admission Deployment (immutable `spec.selector`) so the chart's new one can be created. |

`46` is the instructive case: a plain data patch on a CR that looks exactly like a backfill, but a chart render guard depends on it. Anything whose failure mode is "a HelmRelease goes red" is `pre-apply`, however small the edit.

`background` is for work whose only cost, if it lands late, is that a pre-existing bug persists a while longer:

| migration | what it does, and what late costs |
|---|---|
| `51` | backfills `apps.cozystack.io/application.name` on VM/VL storage PVCs so the post-delete cleanup hook can select them. Late → the PVC leak it fixes persists. |
| `48` | same shape for pre-existing ClickHouse keeper PVCs ([#3057](https://github.com/cozystack/cozystack/issues/3057)). Late → the leak persists. |
| `49` | backfills `tenant.cozystack.io/<ancestor>` labels on tenant namespaces. The chart fix already makes every namespace self-compute its chain on next reconcile; the migration only *accelerates* healing for suspended or failed releases. |
| `44` | removes a leftover `flux-tenants` Deployment, and only once the shard has already drained. Explicitly defensive — flux-shard-operator retires it at runtime regardless. |

### 3. Ordering

Sorting is plain lexicographic byte order over the filename, so the ID grammar is `YYYYMMDD[-NN]-slug` where `NN` is an **optional, two-digit zero-padded** sequence for pinning order within a day:

```
20261014-01-drop-legacy-crds
20261014-02-adopt-onto-new-crds
20261014-unrelated-label-backfill
```

Zero-padding is what makes this work — unpadded, `10` would sort before `2`. CI validates the grammar so an unpadded sequence cannot merge. Two edges: an ID that omits `NN` sorts *after* one that has it on the same date (`0` sorts before any letter), and two PRs both choosing `-01-` do not collide, since the slugs differ and the tie breaks deterministically on slug.

`NN` covers intent and readability but enforces nothing. Where one migration genuinely depends on another — across any dates — it is declared and verified:

```sh
# cozystack-migration: requires=20260801-etcd-crds
```

The runner topologically sorts on `requires` and fails loudly on a missing or cyclic dependency rather than guessing.

Merge order is deliberately not encoded and must not be relied on: a PR merged in June can carry a later date than one merged in July. What the scheme guarantees is that every cluster — fresh install or two years old — walks the same total order.

### 4. Ledger

The existing ConfigMap is extended rather than replaced by a new kind. One key per applied migration, `m.<id>`, whose value carries timestamp, script sha256, and outcome:

```yaml
data:
  version: "56"                                       # legacy scalar, still advanced by integer migrations
  m.20260812-redis-failover-group-label: "2026-08-12T10:04:00Z sha256:abcd… ok"
```

Two properties make a ConfigMap the right container:

- The render gate reads it with Helm `lookup`. `lookup` against a not-yet-Established CRD returns empty, which would resolve the current version to `0` and silently re-render the whole migration Job. A ConfigMap has no such bootstrap edge.
- `hack/update-codegen.sh` ends with a catch-all `mv` that would misroute a new `api/v1alpha1` CRD into the cozystack-controller chart, and both `internal/crdinstall/install_test.go` and `hack/e2e-install-cozystack.bats` hardcode the current two-CRD set. Downstream consumers (`terraform-provider-cozystack`, `ansible-cozystack`) hand-type this API group.

**Write mechanics.** Ledger keys are written with `kubectl patch --type merge`: atomic, no read-modify-write race between concurrent writers. The existing `stamp_cozystack_version` in `migrations/lib/cozystack-version.sh` uses `kubectl apply` and must keep touching **only** `version`. Apply's three-way merge prunes keys that were in last-applied-configuration but absent from the new manifest; patch-written `m.*` keys were never in last-applied, so they survive — subtle enough to deserve a dedicated test. It is the same class of bug migration `42` was written to fix for the `no-delete` label.

### 5. The migration contract

Identity is only half the problem. A migration today is an unconstrained shell script: each author decides what failure means, which shell dialect to use, whether to stamp, and whether to test. Five of fifty-three have tests. `48`, `49` and `51` each independently hand-rolled "best-effort" by scattering `|| true`; `47` and `50` each hand-rolled fail-closed. None of that is machine-readable or checkable.

Shell stays. The contract around it is formalised.

**The runner owns the lifecycle; the migration owns only the change.** Migrations no longer stamp anything. They do not know their own ID, and there is no "next version number" to get wrong.

**Declared metadata, not prose.** A header block the runner and the linter both parse:

```sh
# cozystack-migration: tier=pre-apply        # pre-apply | background
# cozystack-migration: on-error=abort        # abort | warn
# cozystack-migration: requires=20260801-etcd-crds
```

`on-error=warn` turns the "best-effort by design" paragraph into something the runner enforces. The script is then written plainly fail-fast, and the runner decides what a non-zero exit means — instead of `|| true` per command, which also swallows the failures the author wanted to see.

**A shared library, extended.** `migrations/lib/` already holds `cozystack-version.sh` and `seaweedfs-db-adopt.sh`, so the precedent exists. It grows helpers for operations that keep being re-implemented: a `kubectl` wrapper with retry on transient apiserver errors (what the `|| true` sites are really reaching for), a list helper that does not SIGPIPE under `pipefail` (migration `44` documents that trap in a comment), and the Helm-ownership adopt / `resource-policy: keep` pattern shared by `31`, `33`, `35`, `43`, `45` and `53` — by far the most repeated operation in the tree.

**A linter**, `hack/lint-migrations.sh`, wired into `make unit-tests`: filename matches the ID grammar; header present, parseable, and declaring `tier` and `on-error`; one shell dialect (`#!/bin/sh`, since the image is busybox — several migrations are currently `#!/bin/bash` for no stated reason); `shellcheck` clean; `requires` targets exist; no direct writes to the ledger. That last rule is where the architectural guard currently in `hack/cozystack-version-stamp.bats` moves to.

**Tests become mandatory.** Every new ID migration ships a bats suite driving it against fake `kubectl` fixtures, and the linter fails a migration that has none. The pattern is established: `hack/migration-50-etcd-adopt.bats` drives the real script against `hack/testdata/migration-50/` with 28 cases. This is the highest-leverage rule in the set — migration `43`'s hardcoded release name is caught by a single test case using a non-default instance name.

**A scaffold**, `hack/new-migration.sh <slug>`, generating the script and test stub with the header filled in and today's date. Conventions that require reading a document get followed unevenly; conventions the tooling hands you get followed.

### 6. Runner

`run-migrations.sh` gains a second pass, so in-flight integer migrations merge unchanged:

1. **Legacy pass** — unchanged `seq CURRENT (TARGET-1)` over `migrations/[0-9]+`, gated on `version`. Frozen: CI rejects any *new* integer file.
2. **ID pass** — `pending = ls(pre-apply/) − applied − revoked`, topologically sorted, each recorded via patch on success.

Legacy runs first. `background/` is not executed by the hook at all.

### 7. Execution

**Pre-apply** stays the render-gated hook. The gate generalises from a scalar compare to a set difference: the chart already ships the scripts (there is no `.helmignore` in `packages/core/platform`), so `.Files.Glob "images/migrations/migrations/pre-apply/*"` yields the ID list at render with no content reads. The Job is only created when the difference is non-empty.

**Background** is orchestrated — not executed — by cozystack-operator, because the scripts live in the migrations image and the operator has neither them nor a Helm renderer. The chart therefore writes down the two things the operator cannot derive: which IDs are background, and which migrations image the current platform release pins.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cozystack-migrations-index
  namespace: cozy-system
data:
  image: ghcr.io/cozystack/cozystack/platform-migrations:v1.7.0@sha256:…
  background: |
    20260814-clickhouse-keeper-pvc-labels
    20260814-tenant-ancestor-labels
```

The operator reconciles `pending = background − ledger − revoked`, creates one Job per pending ID from `image` with `ONLY=<id>`, and patches the ledger on success. Because the ConfigMap is re-rendered on every platform upgrade, the image ref and the list cannot drift from the release that shipped them. The operator is already `cluster-admin`, so this needs no RBAC change.

Background Jobs run **serially**, one at a time in ID order: it matches the current model, keeps failure attribution unambiguous, and avoids two migrations touching the same objects concurrently.

Two existing patterns carry over to the reconciler: the `Ready`-condition and per-failure-`Reason` vocabulary in `internal/operator/package_reconciler.go`, and the tolerant annotation-bookkeeping idiom in `internal/operator/packagesource_reconciler.go` (`readRecoveryTracking` / `clearRecoveryTracking`), where malformed state degrades to "no prior attempt" instead of wedging the controller.

Moving backfills off the blocking path also fixes a live operational problem: the migration hook carries `helm.sh/hook-delete-policy: before-hook-creation`, so under Flux's retry loop a failing migration destroys its own logs on the next attempt.

### 8. Revocation

An ID file is immutable once it reaches `main` — editing `43` in place is what created the `53` situation. A faulty migration has two populations to serve, and both are handled explicitly.

**Clusters that have not run it yet** must never run it. Its ID goes in `migrations/revoked`, and the runner subtracts that set from pending. It still records `m.<id>: "… revoked"`, so the ledger remains a complete account of what was decided, not only of what executed.

**Clusters that already ran it** need repair, which is a new migration with a new ID handling both the never-ran and the ran-broken states.

```
# 20260812-redis-failover-group-label stamped the wrong operator-group value.
# Superseded by 20260901-redis-failover-group-fix.
20260812-redis-failover-group-label
```

This turns a silent, dangerous edit into an explicit, reviewable one, and closes the never-ran population automatically rather than by remembering to fold the fix back into the original file.

**Companion CI guard:** once an ID file exists on `main`, its content may not change (diff against merge-base). Revocation is the sanctioned escape hatch, so the guard has somewhere to point.

### 9. Package-scoped migrations

A migration that only touches its own package's objects belongs in that package, delivered by that package's own pre-upgrade hook. `52` deletes a linstor-scheduler Deployment; `22` and `27` move CRDs into a `*-crds` release; `43` and `53` hand `Cluster/seaweedfs-db` between two seaweedfs releases. Such a package carries its own `migrations/` directory and a hook invoking the shared runner scoped to it; IDs and ledger keys are namespaced (`m.seaweedfs.20260812-db-adopt`); the contract, linter and required-tests rules are identical. A package that later moves to its own repository takes its migrations and its history with it.

Hooks work as a delivery location precisely because the ledger and the contract sit behind them. A hook on its own fires on every upgrade of its release, keeps no record of what ran or whether it succeeded, and cannot order anything against another chart.

Three classes cannot move and stay platform-level:

- **Cross-package.** `26` moves resources between `extra/monitoring` and `system/monitoring`; `20` applies five packages in sequence. Neither has a single owning release.
- **Must precede a *different* package's operator.** [#3406](https://github.com/cozystack/cozystack/pull/3406) has to label every RedisFailover before `cozystack.redis-operator` upgrades. A hook on the redis *app* package fires too late, since those releases `dependsOn` the operator. This class looks package-scoped and is not.
- **Must precede the platform's own artifacts**, which is where the runner already sits.

The boundary is ownership of the objects being touched, not which app the change is about.

### 10. Converting the legacy set

The dual-mode runner is a transition device, not an end state. Leaving fifty-three integers in place forever means the runner keeps two code paths, `targetVersion` never goes away, and every new contributor has to learn a scheme that is already deprecated.

The conversion is itself a migration of the migrations, and it is mechanical:

1. Rename `migrations/1..53` to IDs derived from the release each shipped in, preserving order — `20250409-01-mariadb-operator-secrets` through `20260724-53-seaweedfs-db-adopt-repair`. The numeric suffix in the slug keeps the original sequence readable and guarantees the sort matches the old order exactly.
2. Commit the integer → ID mapping as `migrations/legacy-map`, one `N <id>` pair per line.
3. On first run against a cluster that has a `version` scalar but no `m.*` keys, the runner seeds the ledger from the map: every integer below `version` gets its `m.<id>` recorded with outcome `legacy` (no checksum — those files were not immutable when they ran). Idempotent, since the seeding condition is the absence of `m.*` keys.
4. Delete the legacy pass, `targetVersion`, and `hack/check-migrations-target.sh`.

Only step 3 touches clusters, and it writes ledger keys rather than running anything. A cluster stamped `54` ends up with fifty-three `legacy` records and behaves identically.

### 11. Retention

Today every migration ever written ships in the image forever, because a dense sequence cannot have holes: deleting file `7` breaks the `seq` loop that walks past it. Under a set model there is no sequence to break, so migrations can be deleted.

What makes deletion safe is declaring a floor — the oldest platform version this release accepts as an upgrade source, which is already a supported-versions policy decision rather than a new one. Migrations that only ever applied to clusters below the floor are deleted; the runner compares the cluster's ledger against the floor and **refuses**, rather than warning, on a cluster beneath it, pointing at a staged upgrade through an intermediate release. Refusing is the only defensible choice: a warning that is ignored produces exactly the silent-skip failure this proposal exists to eliminate.

### 12. What this retires

`hack/check-migrations-target.sh` and `targetVersion`, the cross-branch slot-alignment check proposed in [#3534](https://github.com/cozystack/cozystack/issues/3534), and the question of which PR owns a number. Backports become safe by construction: an ID applied on `release-1.5` is recorded, and the 1.6 upgrade skips it and runs its own.

## User-facing changes

**Cluster administrators.** No change to the upgrade command or its behaviour. The `cozystack-version` ConfigMap gains one key per applied migration, so `kubectl get configmap -n cozy-system cozystack-version -o yaml` becomes a readable account of what ran and when — today it shows a bare integer. Background migrations become visible as Jobs in `cozy-system` and as conditions on the operator, where today they are invisible steps inside one blocking hook.

**Tenants.** None. No tenant-facing API, CR shape or dashboard surface changes.

**Contributors.** The authoring workflow changes materially and is the main documentation deliverable: `hack/new-migration.sh <slug>` scaffolds the script and its test, the header block is mandatory, a bats suite is mandatory, and the linter runs in `make unit-tests`. `docs/agents/contributing.md` gains a migrations section; the "pick the next number and bump `targetVersion`" instruction is removed wherever it appears.

## Upgrade and rollback compatibility

**Existing clusters.** The ledger seeds from the existing `version` scalar, so a cluster stamped `54` is treated as having applied legacy migrations `1..53` and nothing else. No backfill migration is required — the seeding is runner logic, not a migration, which matters because migrations are the thing being refactored.

**Phase 1 is behaviour-preserving.** Every migration is `pre-apply` and the legacy pass is untouched, so a cluster upgrading through Phase 1 does exactly what it does today.

**Rollback.** Rolling the platform back to an earlier release leaves the ledger keys in place. A rolled-back release simply does not know some IDs, so it computes a smaller pending set and skips them; on rolling forward again they are already recorded and are not re-run. This is strictly better than the scalar, where a rollback that lowered `version` would re-run migrations. Ledger keys are additive and never removed on downgrade.

**Irreversibility.** Revocation is not a rollback: it prevents a migration from running on clusters that have not yet run it, and does nothing to clusters that have. Repair of the latter always requires a new migration. This is flagged explicitly because it is the property most likely to be misread under incident pressure.

**Phase 5** deletes `targetVersion`, which is a breaking change for anything reading it — see Testing for the upgrade-lane assertion that must be replaced first.

## Security

No new trust boundary. The migration hook already runs with `cluster-admin` via a per-hook ServiceAccount and ClusterRoleBinding created and deleted around the Job, and cozystack-operator is already bound to `cluster-admin`; the background tier reuses the operator's existing identity rather than introducing another privileged principal. Migrations are, and remain, arbitrary root-equivalent code shipped inside a digest-pinned image.

The `cozystack-version` ConfigMap carries `platform.cozystack.io/no-delete: "true"` and is guarded by the deletion-protection ValidatingAdmissionPolicy; the added `m.*` keys inherit that protection. No new tenant-supplied input reaches any migration, no new secrets are stored or transmitted, and no new RBAC surface is created.

One property improves: the ledger records a sha256 of each script as it ran, so a migration whose content changed after being applied is detectable on a live cluster rather than only by reading git history.

## Failure and edge cases

- Migration file missing from the image but pending → runner exits non-zero and refuses to advance, preserving today's behaviour. A missing file indicates a packaging mistake and must fail loudly rather than stall the cluster at a stale state.
- Migration already recorded in the ledger → skipped, not re-run, on every subsequent upgrade.
- Revoked ID → skipped, and recorded as `revoked` so the ledger stays complete.
- `on-error=abort` migration fails → the hook Job fails, the platform upgrade does not proceed, and nothing is recorded for that ID.
- `on-error=warn` migration fails → logged, upgrade proceeds, and the ID is recorded with a non-`ok` outcome so it is visible rather than indistinguishable from success.
- Applied migration's file content changed since it ran → checksum mismatch reported. The migration is not re-run; repair is a new ID.
- `requires` names an ID that does not exist, or the graph has a cycle → runner fails before executing anything.
- Two migrations dated the same day with no `NN` and no `requires` → both run, in deterministic slug order, identically on every cluster.
- Cluster with `version` set and no `m.*` keys (Phase 5) → seeded from `legacy-map`; running twice seeds nothing further, since the condition is the absence of `m.*` keys.
- Background Job fails → surfaces as a `Ready=False` condition with a distinct reason on the operator, and is retried on the next reconcile rather than blocking the upgrade. Whether repeated failure should eventually stop retrying is an open question below.
- Cluster below the retention floor (Phase 5+) → runner refuses and names the intermediate release to upgrade through.

## Testing

Test-first throughout. The runner is a shell script driven against fake `kubectl` fixtures, a format already established by `hack/migration-50-etcd-adopt.bats` (28 cases against `hack/testdata/migration-50/`) and `hack/migration-seaweedfs-db-adopt.bats`.

**Unit (bats, `hack/cozytest.sh`).** Runner: every case in *Failure and edge cases* above. Ordering: `20261014-02-*` runs after `-01-` and before `-10-`; a `requires` edge overrides lexicographic position; unpadded sequences are rejected. Ledger durability: `stamp_cozystack_version` after `record_migration` must not prune `m.*` keys. Linter: missing or unparseable header, undeclared `tier`/`on-error`, `#!/bin/bash`, a ledger write from inside a migration, a migration with no bats suite, a dangling `requires` — each rejected.

**Unit (helm-unittest).** Render gate: nothing pending → zero documents; legacy-only pending; ID-only pending; both. The existing `packages/core/platform/tests/migration_hook_skip_backup_test.yaml` asserts the hook's env by positional index, so it is rewritten alongside.

**Controller (envtest).** Pending computation including revoked, Job creation, ledger patch on success, and a failing Job surfacing as `Ready=False` with a distinct reason rather than a retry storm.

**E2E (upgrade lane).** [#3276](https://github.com/cozystack/cozystack/pull/3276) currently asserts the stamp reached `migrations.targetVersion`. Its replacement, required before Phase 5 retires `targetVersion`, is an **empty-pending assertion**: every ID under `pre-apply/` and `background/` in the tree under test must have a matching `m.<id>` ledger key whose outcome is `ok`, `legacy` or `revoked`. Both sides are cheap — `ls` the two directories in the checked-out tree, read the ConfigMap's keys — and it is strictly stronger than the integer compare, because it proves each individual migration reached a terminal state rather than that a counter moved. The background half is asynchronous, so it polls; any other outcome fails the lane and names the offending ID.

Existing suites that pin the current shape and are rewritten rather than ported: `hack/cozystack-version-stamp.bats` (its architectural guard hardcodes the `>= 42` numeric-filename convention; it becomes a linter rule), and the literal next-version assertions (`51`, `54`) inside `hack/migration-50-etcd-adopt.bats` and `hack/migration-seaweedfs-db-adopt.bats`, which disappear with migration-side stamping. `hack/migration-seaweedfs-db-adopt.bats` also `sed`s `FROM alpine:` out of the Dockerfile, so that line's shape must be preserved.

[#3458](https://github.com/cozystack/cozystack/pull/3458) adds a second ordered runner (`preflight/`) to the same image at hook weight 0. It is a co-tenant and is not disturbed by this work.

## Rollout

Each phase is independently shippable.

**Phase 1 — engine and contract.** Dual-mode runner, `record_migration`, set-difference render gate, header parser and `on-error` handling, `hack/lint-migrations.sh`, `hack/new-migration.sh`, `lib/` helpers, and three CI guards (integer freeze, ID immutability, ID grammar). Every migration is `pre-apply`, so runtime behaviour is unchanged. The linter enforces on `pre-apply/` and `background/` only; the 53 frozen integers are grandfathered.

**Phase 2 — background tier.** Index ConfigMap template and the operator reconciler. Applies to new work only; the legacy integers stay frozen where they are, since `44`, `48`, `49` and `51` are already applied essentially everywhere.

**Phase 3 — package scoping.** Scoped discovery and namespaced ledger keys, plus a package hook that renders only when that package has pending IDs. Piloted with one low-risk, genuinely package-scoped migration — `52` (linstor-scheduler) is the smallest — before offering the pattern more widely.

**Phase 4 — attrition.** Reduces the rate at which new migrations are needed; retires none of the existing ones. *Admission-time defaulting* for fields on objects Cozystack does not template: migration `51` exists because vm-operator stamps `volumeClaimTemplate` labels only onto PVCs created after the chart change, and the StatefulSet controller never re-labels an existing PVC — Cozystack cannot template those PVCs, a controller creates them. A `MutatingAdmissionPolicy` matching `CREATE` on `persistentvolumeclaims` owned by a cozystack-managed VMCluster stamps the label at creation. It must be capability-gated exactly as `packages/system/cozystack-basics/templates/ingress-hostname-policy.yaml` gates its VAP on `.Capabilities.APIVersions.Has`, because the management cluster floor is 1.33 while `MutatingAdmissionPolicy` is beta in 1.34 and GA in 1.36. It is prevention only — existing PVCs still need the one-shot backfill. *API-layer conversion* for the values-format class: migration `39` rewrites flat `resourcesPreset` names into instance-type names across every App CR, and `pkg/apis/apps/presets/legacy.go` records that the same table is mirrored in four places. Accepting the legacy spelling in the aggregated API's conversion and defaulting layer means the stored CR never has to be rewritten and roughly fifteen migrations of this shape stop existing. Each half warrants its own proposal.

**Phase 5 — legacy conversion and retention.** Rename the integer set, commit `legacy-map`, implement seeding, delete the legacy pass and `targetVersion`. Depends only on Phase 1 plus at least one shipped release of soak, so the ID path is exercised before the seeding path is added; independent of Phases 2–4. The retention floor is introduced in the first release that actually deletes a migration, not before.

## Open questions

- Once packages genuinely split into separate repositories, does each keep its own ledger ConfigMap in its own namespace, or does a single platform ledger remain the system of record? A per-package ledger is the cleaner ownership boundary; a single one is easier to inspect when diagnosing an upgrade.
- Should `on-error=warn` migrations that fail be retried on the next upgrade, or recorded as attempted-and-failed and left alone? Retrying suits transient apiserver errors; not retrying avoids an indefinitely repeating failure nobody is watching.
- Does `requires` need to express "must run in a strictly earlier upgrade" as distinct from "must run earlier in this pass"? No current migration needs it, and adding it later is not a breaking change.

## Alternatives considered

**Data model — a CRD ledger with operator status.** Rejected for the two reasons in §4: `lookup` against a not-yet-Established CRD fails silently rather than loudly, and `hack/update-codegen.sh`'s catch-all `mv` plus the hardcoded CRD lists in `internal/crdinstall/install_test.go` and `hack/e2e-install-cozystack.bats` make adding a kind to `cozystack.io` a non-local change with downstream consumers.

**Data model — keep the scalar, add a cross-branch CI check.** This is the mitigation [#3534](https://github.com/cozystack/cozystack/issues/3534) itself proposes. Rejected as the primary fix: it detects divergence rather than preventing it, requires every supported branch to be checked out at review time, and leaves slot contention and the `targetVersion` lint untouched.

**Runtime — per-package Helm hooks with no ledger.** Rejected: a hook fires on every upgrade of its release, so one-shot work needs its own state anyway; there is no record of what ran or whether it succeeded; and there is no ordering across charts, which the cross-package migrations require. Hooks are adopted as a *delivery* mechanism in Phase 3, on top of the ledger.

**Runtime — operator-driven execution for everything.** Rejected: it loses the runs-before-chart-apply guarantee, and the migrations that most need that guarantee are exactly the destruction-prevention ones in §2.

**Runtime — Kyverno `mutateExisting` for the backfill class.** Rejected: it adds a dependency, and its existing-resource mutation is explicitly asynchronous with variable delay — disqualifying wherever the mutation must land before a Helm prune.

**Schema — `StorageVersionMigration` (`storagemigration.k8s.io`).** Not applicable: still alpha/beta in the Kubernetes versions Cozystack supports, and App objects are served by an aggregated API server rather than stored as CRDs.

**Language — rewriting migrations in Go.** Rejected: the operations are `kubectl` calls, and shell keeps them reviewable and testable against fake-binary fixtures without a build step in the image. The problem is the absence of a contract, not the language.
