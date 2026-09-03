# Platform migration engine

- **Title:** `Platform migration engine: content-addressed IDs, an applied-set ledger, and an authoring contract`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-08-12`
- **Status:** Draft

## Overview

Platform migrations are identified by a dense global integer that also serves as cluster state. Every new migration must claim the next number, which makes concurrent migration PRs conflict by construction; the single scalar recording progress cannot describe a branched history, which makes migrations unbackportable; and the pending set is computed from a number maintained in a different file than the migrations themselves, which makes silent skips possible.

This proposal replaces the integer with a content-addressed ID and an applied-set ledger, splits execution into a blocking tier and a background tier, and puts a machine-checkable contract around migration authoring. Slot contention disappears, backports become safe by construction, and the "migration silently did not run" failure stops being expressible.

## How it works

A migration is a shell script under `pre-apply/` or `background/` whose filename is a slug and nothing else: `redis-failover-group-label`. That slug is its identity for the life of every cluster — it is the key a cluster records when the migration runs, it is the same on every branch, and it never changes. Two people adding migrations at the same time pick two different slugs and touch nothing in common, so there is no number to claim and nothing to renumber on a rebase.

When a release is cut, CI writes one immutable file into `order.d/` listing the slugs that release is the first to ship. Those files only ever accumulate, so reading them in order gives the execution order, and each release's list begins with the previous release's list unchanged. That is what makes clusters agree: a cluster upgrading 1.6 → 1.8 in one hop walks the same list in the same order as one that went through 1.7. A migration written long ago but merged late sits at the end, where the release that shipped it put it, rather than in the middle where its authoring date would have put it.

On the cluster, ConfigMap `cozy-system/cozystack-version` gains one key per migration that has reached a terminal state, plus a `complete-through` watermark naming the newest fully-settled batch. The runner reads the list once, skips anything already recorded, revoked or retired, and runs the rest — no sorting, no dependency resolution and no version arithmetic, because the release cut settled all three. A blocking migration runs in the existing `pre-upgrade` hook Job before the platform chart applies; a non-blocking one is run afterwards by cozystack-operator, one Job each. A fresh install records every slug it ships as skipped rather than running it, which is what a new cluster does today.

## Scope and related proposals

- [`kubernetes-nodes-split`](../kubernetes-nodes-split/) — its Phase 2 migration is used here as a worked example of the blocking tier. No ordering dependency between the two.
- Phase 4 below sketches two mechanisms for reducing the *rate* at which migrations are needed (admission-time defaulting, API-layer conversion). Each is large enough to deserve its own proposal; they are included here only to show where the migration count goes over time.
- Carrying migrations out of the monorepo alongside their package would need a scoping mechanism this proposal deliberately does not offer; §9 records why package scoping was withdrawn. Nothing here depends on decomposition work.

## Context

Migrations live in `packages/core/platform/images/migrations/` and are delivered as a Helm `pre-upgrade,pre-install` hook Job from `packages/core/platform/templates/migration-hook.yaml`, running with `cluster-admin`. All migration scripts are baked into a single image, pinned by digest in `packages/core/platform/values.yaml`.

`run-migrations.sh` walks `seq $CURRENT_VERSION $((TARGET_VERSION - 1))`. `CURRENT_VERSION` comes from a scalar in ConfigMap `cozy-system/cozystack-version`; `TARGET_VERSION` is hand-maintained in `values.yaml`. The chart's `templates/cozystack-version.yaml` creates that ConfigMap on first install, and the hook only renders when `currentVersion < targetVersion`, so a cluster with nothing pending never starts a pod.

At the time of writing there are 56 migrations on disk and `targetVersion` is 57. That count is a snapshot and this document does not keep it current — it is quoted because the scale is what the argument rests on, and every count below reads the same way.

### The problem

One integer carries both a migration's identity and the cluster's progress through the sequence. Three consequences follow.

**Slot contention.** Every new migration must claim the next integer, so every pair of concurrent migration PRs conflicts. [#3406](https://github.com/cozystack/cozystack/pull/3406), [#3379](https://github.com/cozystack/cozystack/pull/3379) and [#3315](https://github.com/cozystack/cozystack/pull/3315) all added `migrations/54` when this document was written. All three have since merged, as `54`, `55` and `56`, two of them renumbering on the way in; `targetVersion` is now 57. Renumbering is not a rename — it also means re-checking `targetVersion` and cross-branch alignment. The contention resolved itself the expensive way, in three PRs, while a document about it sat in review.

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

- Rewriting the 56 existing integer migrations' logic. They are already applied on essentially every cluster; their content is frozen. They are renamed, but only mechanically, in Phase 5.
- Moving migrations out of shell. The operations are `kubectl` calls, and shell keeps them reviewable and testable against fixtures.
- Replacing the `cozystack-version` ConfigMap with a new API kind. It is extended in place.

## Design

### 1. Identity and layout

Identity is the **slug** and nothing else: `redis-failover-group-label`. No date, no sequence number, no hash in the filename. The slug is the ledger key and is the one thing about a migration that may never change.

That is forced rather than chosen. §3 shows that execution order cannot be derived from anything an author knows while writing the migration, so an ordinal in the name is a claim the name cannot keep, and maintaining it costs a rename on every stale rebase. Removing it also removes the only reason two concurrent PRs would ever have to look at each other.

Grammar is `^[a-z0-9]+(-[a-z0-9]+)*$`, at most 60 characters, unique across **both** tiers — the ledger key has no tier dimension, and a migration may be moved between tiers before it ships. Deriving identity from a path instead would mint a fresh identity on every move.

Tier is the **directory**, so nothing about tiering is parsed at render time.

The integer set does not survive alongside the new one. It is carried untouched only while the conversion is staged, and §10 renames all of it, after which there is one identity scheme, one runner path and nothing a contributor has to know about integers. Leaving them in place permanently is explicitly rejected: it would mean a dual-mode runner forever, `targetVersion` forever, and every new contributor learning a deprecated scheme next to the live one in order to add a migration.

During the transition:

```
packages/core/platform/images/migrations/migrations/
  1 .. 56            # transitional only — frozen, never extended, converted by §10
  lib/
  revoked            # slugs that must not run (§8)
  order.d/           # release-assigned execution order (§3), machine-written only
    00001-v1.6.0
    00002-v1.6.3
  pre-apply/         # blocking, runs in the pre-upgrade hook Job
    redis-failover-group-label
  background/        # non-blocking, run by cozystack-operator
    clickhouse-keeper-pvc-labels
```

After the conversion, which is the shape to design against:

```
packages/core/platform/images/migrations/migrations/
  lib/
  revoked
  order.d/
    00000-legacy                             # the converted integer set, in its original order
    00001-v1.6.0
    …
  pre-apply/
    mariadb-operator-secrets                 # was `1`
    …                                        # one file per converted integer
    redis-failover-group-label
  background/
    clickhouse-keeper-pvc-labels
```

### 2. Tiers

The two tiers already exist informally, written into the migration headers. Migrations `48`, `49` and `51` each carry the same sentence: *"Best-effort by design: the worst case if a relabel is skipped is the pre-existing bug this PR set out to fix, which is strictly no worse than the status quo."* That is the background criterion, reasoned about case by case in prose. This proposal makes it declarable and executable.

**`pre-apply` is the default**, and the test is not "is this a backfill" — it is *can something be named that breaks if this runs late?* Chart render guards, operator label selectors and Helm prunes all count:

| migration | what depends on it having already run |
|---|---|
| `56` ([#3315](https://github.com/cozystack/cozystack/pull/3315)) worker pools | creates the child `kubernetes-nodes-<cluster>-<pool>` HelmReleases before the kubernetes chart stops rendering worker objects. Skip it and Helm prunes the tenant's running worker VMs. |
| `55` ([#3379](https://github.com/cozystack/cozystack/pull/3379)) FluxCD addon removal | suspends and drops the finalizer on tenant Flux HelmReleases so the delete does not uninstall Flux *inside* the tenant cluster. |
| `54` ([#3406](https://github.com/cozystack/cozystack/pull/3406)) RedisFailover | stamps the operator-group label and pins the engine image before redis-operator v3.3.5 rolls. An unlabelled CR never reaches the new operator's informer at all — no event, no error. |
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

Order is assigned when a release is cut, because that is the only moment the necessary fact exists.

**Why nothing else can carry it.** Call the release that first shipped a migration its **shipping release**. Take two migrations with different shipping releases, and a cluster that upgrades through the earlier one and later through the later one. On the first hop the earlier migration is pending and the later one is not in that image at all, so this cluster runs them in shipping-release order — it had no other option available to it. Every other cluster has to match, or they disagree. That is the entire requirement, and it can be stated in one line:

> The execution order must never contradict shipping-release order.

Nothing an author knows while writing a migration tells them its shipping release, because it depends on which release happens to be cut next and on whether the PR is still in review by then. A date, a name, a checksum of the contents, a random identifier, a declared dependency — none of these has any relationship to shipping-release order, so each of them agrees with it only by accident. The failure is not hypothetical:

> A PR opened in July sits in review for months. A second PR, opened in August and knowing nothing about the first, merges ahead of it and ships in 1.7. The July PR is then rebased, merges, and ships in 1.8. A cluster going 1.6 → 1.7 → 1.8 runs the August migration first, because in 1.7 the July one does not exist. A cluster going 1.6 → 1.8 has both pending at once and runs whichever the key sorts first. Under any authoring-time key those two clusters disagree, and no dependency was declared because the July author never knew the August PR existed.

**A dependency graph does not rescue this.** The idea is the natural one: declare edges between migrations, topologically sort the whole set once, and have every cluster run its pending migrations in that single order. It looks sound, because given one fixed list any two clusters that both run `X` and `Y` take their relative order from the same place.

There is no one fixed list. Each release's image holds a different set of migrations, so each release produces a different sorted list. And sorting by declared dependencies does not produce one answer, it produces *an* answer: for a pair with no edge between them the dependency rule has no opinion about which comes first, and yet a list must put one of them somewhere. Some other rule has to decide, and in practice that rule is alphabetical order of the file names. Since almost no pair of migrations has a declared edge, that fallback rule is what actually produces the order. Worked through:

- 1.7's image holds `etcd-crds-precreate` and `vm-disk-bus-default`, with no dependency declared between them. The fallback rule applies, so the list is alphabetical — `etcd-crds-precreate`, `vm-disk-bus-default` — and a cluster reaching 1.7 runs both.
- 1.8 adds `vm-cloudinit-drive`, again with no edge. 1.8's list is now `etcd-crds-precreate`, `vm-cloudinit-drive`, `vm-disk-bus-default`: the new migration sorted into the **middle**.
- The cluster already at 1.7 has `vm-disk-bus-default` recorded, so at 1.8 it runs `vm-cloudinit-drive` *after* it. A cluster arriving at 1.8 from 1.6 runs the list as written and takes `vm-cloudinit-drive` *before* it. They disagree — and the graph played no part, because there were no edges to play one.

So the requirement lands on the fallback rule, not on the dependencies: **adding a migration must only ever extend the order at its end, and never insert into its middle.** Alphabetical order of names does not do that, since a new name can land anywhere in the alphabet. Ordering by date does not either — that is the July PR inserting ahead of August. A batch number does, because a newly added migration always receives the newest batch, which is by definition at the end.

**The manifest.** Order lives in `order.d/`, one file per release cut, created and never afterwards modified or deleted:

```
order.d/00003-v1.7.0-rc.1
```

```
# batch 00003, sealed at v1.7.0-rc.1 by hack/seal-migration-batch.sh
clickhouse-keeper-pvc-labels
vm-pool-adopt
etcd-crds-precreate
```

Bare slugs, one per line. Tier is not repeated — it is the directory the script lives in, and duplicating it here would let the two drift.

**Within a batch, nothing needs assembling.** Two migrations sealed in the same batch are seen by every cluster in one pass off one image, so the agreement requirement says nothing about their relative order and any deterministic rule satisfies it. That collapses the release step to *assigning a label*: no reconstruction of merge order, no rank counter, no git archaeology. The rule is alphabetical order of the slugs, then reordered where a `requires` in the same batch demands it.

**The prefix invariant, which is the whole guarantee in one check.** Because batches only ever append, each release's manifest is a literal prefix of the next one's. So the property this section exists to deliver is verified at every cut by comparing the head of the new manifest against the previous release's, byte for byte. Under `order.d/` the invariant is additionally structural: add-only is a property of the filesystem, visible in `git diff --name-status`, rather than a lint asserting that the first N lines of a file did not change.

**What the release cut does.** `hack/seal-migration-batch.sh <tag>`, run before the image build:

1. `new` = slugs present under `pre-apply/` and `background/` and absent from every existing batch.
2. If `new` is empty, write nothing.
3. Sort `new` alphabetically, then reorder to satisfy any `requires` edges among them, keeping alphabetical order everywhere those edges say nothing; fail on a cycle, on a `requires` naming a slug that does not exist, and on a `requires` pointing at a *later* batch.
4. Write `order.d/<max+1, zero-padded>-<tag>`.
5. Refuse if any earlier batch file differs from its committed content, or if any manifest entry names a slug that neither exists on disk nor is flagged `retired` (§11).

`cut-prerelease.yaml` is the single entry point for pre-release tags and dispatches from the branch the tag belongs to, and `tags.yaml` already performs `git add . && git commit -m "Prepare release <tag>"` on that branch — so the batch file has an existing, serialised, CI-owned slot to be written into. No PR author writes a manifest line, and CI rejects a PR that adds one.

**`requires`: who writes it, when, and what it can say.** It is written by the migration's author, in that migration's own header, at the time the migration is written, and it may only name a slug that **already exists in the tree** — CI rejects a dangling target. That bounds what it can express: a dependency on something already merged, never on something not yet written. Which is exactly why the scenario above cannot be fixed by declaring anything.

What the edge does depends on where its target already sits. If the target was sealed in an earlier batch, the dependency is satisfied before the runner starts and `requires` acts purely as a check. If both are still unsealed they land in the same batch, and the release step uses the edge to order them within it. Either way it is resolved at the cut and frozen into the manifest, so the runner parses no headers and evaluates no dependencies. What remains is the value worth keeping: a declared dependency CI can verify, which fails loudly instead of running in the wrong order.

**What is guaranteed, and what is not.** Every cluster upgrading along one release line runs any two migrations in the same relative order. Cross-line, order may differ for exactly one class: a migration backported to a maintenance branch, which is assigned a batch on that branch and a later batch on `main`. Trying to reconcile the two would mean splicing an entry into the middle of `main`'s already-sealed manifest, which is precisely what the prefix invariant forbids — so it is not reconciled. That divergence is safe by the same reasoning that justified the backport: it is the maintainer's assertion that the migration is correct on a branch missing everything in between. CI reports the affected pairs by diffing `order.d/` across branches, so the assertion is explicit rather than assumed. Today the same divergence exists and is invisible — [#3534](https://github.com/cozystack/cozystack/issues/3534) was found by hand.

**A backport, worked through.** `kubeadm-keep-policy` is authored on `main` and is needed on the 1.5 line too.

1. The PR merges to `main`. No release has been cut since, so no batch names it on either branch.
2. `.github/workflows/backport.yaml` cherry-picks it onto `release-1.5`: same path, same slug, same bytes. **The author does nothing beyond the cherry-pick** — nothing to renumber, nothing to rename, no manifest to edit, because `order.d/` does not mention the slug yet.
3. `v1.5.4-rc.1` is cut from `release-1.5`. That branch's manifest gains a batch containing `kubeadm-keep-policy`.
4. `v1.7.0-rc.1` is cut from `main`. Main's manifest gains its own batch, also containing `kubeadm-keep-policy`, which was never sealed there.

Two clusters then differ in order and agree in effect:

```
1.5.3 → 1.5.4 → 1.7.0    mariadb-operator-secrets, kubeadm-keep-policy, monitoring-move
1.5.3 → 1.7.0            mariadb-operator-secrets, monitoring-move, kubeadm-keep-policy
```

`kubeadm-keep-policy` executes **exactly once** on both paths, because the ledger key is the slug and the slug is identical on both branches. That is [#3534](https://github.com/cozystack/cozystack/issues/3534) eliminated: the situation that today stamps a cluster past a migration it never ran.

What differs is its position relative to `monitoring-move`, a 1.6 migration the 1.5 line never carried. Reconciling that would mean splicing the slug into `main`'s manifest at its 1.5.4 position — inside batches already sealed and shipped, which the prefix invariant forbids. So it is not reconciled, and the divergence is the backport's own premise: cherry-picking to `release-1.5` asserted the migration is correct on a branch holding none of 1.6.

### 4. Ledger

The existing ConfigMap is extended rather than replaced by a new kind. One key per applied migration, `m.<slug>`, whose value carries timestamp, script sha256, and outcome:

```yaml
data:
  version: "57"                     # legacy scalar, still advanced by integer migrations
  complete-through: "00002"         # watermark: a batch number (§3), not a date
  m.redis-failover-group-label: |
    {"state":"ok","at":"2026-08-12T10:04:00Z","sha256":"abcd…",
     "batch":"00002","image":"ghcr.io/…@sha256:…","attempts":1}
  m.clickhouse-keeper-pvc-labels: |
    {"state":"failed","at":"2026-08-14T09:11:00Z","sha256":"ef01…",
     "batch":"00003","image":"ghcr.io/…@sha256:…","attempts":1,
     "reason":"ScriptError","message":"3 of 812 PVCs rejected the label patch"}
```

The key is the bare slug. It carries no tier and no path, so moving a migration between `pre-apply/` and `background/` before it ships does not mint a second identity for the same work.

**The value is structured, not a sentence.** Four things parse it — the runner, the linter, the E2E assertion and compaction — and a positional string is how four slightly different parsers come into existence. It is JSON: `jq` is installed in the migrations image, so the runner can both read and build it without string surgery. `state` is one of `ok`, `legacy`, `revoked`, `skipped`, `failed`; `batch` records where §3 placed the migration; `image` records what actually executed, which matters because a background migration pending since 1.7 runs under 1.8's image; `attempts` counts executions of that migration on this cluster, and its only source is the manual path below — an entry is written with `1` and grows only when an operator asks for a re-run, which makes "this has already been tried three times" visible before anyone tries a fourth. A failure additionally carries `reason` and a human `message`. The exact field set will move during implementation and that is fine — what is settled here is that the value is parsed, not read.

One mechanical consequence worth stating because it is easy to get wrong: the value is a JSON document inside a YAML string inside a `kubectl patch --type merge` payload, so it must be built with `jq -n` and passed as data, never assembled by concatenating shell variables.

**The watermark cannot hide a migration that still ships.** `complete-through` is the highest batch every one of whose entries has reached a terminal state — recorded in the ledger, or flagged `retired` (§11). It is a **batch number**, which is assigned at a release cut and always as the next one up, so a migration merged years after it was written still receives the newest batch. There is no value a newly merged migration can be given that sits below an existing watermark. Contrast a date: a July-dated file merging after the watermark passed July is below it and is treated as applied, which is exactly the silent skip this proposal exists to remove. golang-migrate shows the failure in a shipped library: its single `version, dirty` row makes a late lower-numbered migration permanently unreachable, with no error and no override.

The watermark is computed across **both** tiers. Scoping it to `pre-apply` alone would let it advance past a background migration that never finished, and the cluster would then be told it is complete when it is not.

A `failed` entry is terminal for the watermark's purposes and does not pin it. Since nothing is retried automatically (§7), pinning the watermark on a failure would stall retention on that cluster indefinitely for a migration nobody is coming back to. The failure stays visible instead, in its own entry, which is never compacted at any age.

**The ledger does not grow without bound.** Compaction runs only at the retention floor (§11), removing the individual keys the watermark already covers. Records that did not reach `ok` — a `warn` failure, a `revoked` decision — are never compacted, whatever their age, because those are the entries someone will go looking for.

Two properties make a ConfigMap the right container:

- The render gate reads it with Helm `lookup`. `lookup` against a not-yet-Established CRD returns empty, which would resolve the current version to `0` and silently re-render the whole migration Job. A ConfigMap has no such bootstrap edge.
- `hack/update-codegen.sh` ends with a catch-all `mv` that would misroute a new `api/v1alpha1` CRD into the cozystack-controller chart, and both `internal/crdinstall/install_test.go` and `hack/e2e-install-cozystack.bats` hardcode the current two-CRD set. Downstream consumers (`terraform-provider-cozystack`, `ansible-cozystack`) hand-type this API group.

**Write mechanics.** Ledger keys are written with `kubectl patch --type merge`: atomic, no read-modify-write race between concurrent writers. The existing `stamp_cozystack_version` in `migrations/lib/cozystack-version.sh` uses `kubectl apply` and must keep touching **only** `version`. Apply's three-way merge prunes keys that were in last-applied-configuration but absent from the new manifest; patch-written `m.*` keys were never in last-applied, so they survive — subtle enough to deserve a dedicated test. It is the same class of bug migration `42` was written to fix for the `no-delete` label.

### 5. The migration contract

Identity is only half the problem. A migration today is an unconstrained shell script: each author decides what failure means, which shell dialect to use, whether to stamp, and whether to test. Most of the fifty-six have no test at all, though the three newest each shipped with a bats suite. `48`, `49` and `51` each independently hand-rolled "best-effort" by scattering `|| true`; `47` and `50` each hand-rolled fail-closed. None of that is machine-readable or checkable.

Shell stays. The contract around it is formalised.

**The runner owns the lifecycle; the migration owns only the change.** Migrations no longer stamp anything. They do not know their own ID, and there is no "next version number" to get wrong.

**Declared metadata, not prose.** A header block the runner and the linter both parse:

```sh
# cozystack-migration: tier=pre-apply        # pre-apply | background
# cozystack-migration: on-error=abort        # abort | warn
# cozystack-migration: requires=etcd-crds-precreate     # optional; must already exist (§3)
```

`on-error=warn` turns the "best-effort by design" paragraph into something the runner enforces. The script is then written plainly fail-fast, and the runner decides what a non-zero exit means — instead of `|| true` per command, which also swallows the failures the author wanted to see.

**A shared library, extended.** `migrations/lib/` already holds `cozystack-version.sh` and `seaweedfs-db-adopt.sh`, so the precedent exists. It grows helpers for operations that keep being re-implemented: a `kubectl` wrapper with retry on transient apiserver errors (what the `|| true` sites are really reaching for), a list helper that does not SIGPIPE under `pipefail` (migration `44` documents that trap in a comment), and the Helm-ownership adopt / `resource-policy: keep` pattern shared by `31`, `33`, `35`, `43`, `45` and `53` — by far the most repeated operation in the tree. It also grows a fleet-iteration helper: given an app kind, walk every instance of it and apply the same operation to each, carrying the errexit handling `lib/seaweedfs-db-adopt.sh` documents so one instance failing neither aborts the sweep silently nor passes unnoticed. That helper is the direct answer to the `43` class (§9) and is the one worth writing first.

**A linter**, `hack/lint-migrations.sh`, wired into `make unit-tests`: filename matches the slug grammar of §1; header present, parseable, and declaring `tier` and `on-error`; one shell dialect (`#!/bin/bash`: the image is `alpine` with `bash` explicitly installed, so both dialects run today and the set is split 44 to 12 between them; the legacy files are frozen and cannot be restated, so the rule binds new migrations only, and bash is the choice because it is what the most recent migrations already adopted deliberately and because ash's divergences surface on a cluster rather than in CI); `shellcheck` clean; `requires` targets exist and name no later batch; no direct writes to the ledger; and no file added under `order.d/` by anything but the release job, which is the rule that keeps §3's guarantee structural rather than cultural. That last rule is where the architectural guard currently in `hack/cozystack-version-stamp.bats` moves to.

**Every migration must be safe to run twice.** This is a contract rule rather than a tier: since nothing is retried automatically and an operator may re-run any migration by hand (§7), re-running has to be safe for all of them, not for a declared subset. The precedent is already in the tree — `packages/core/platform/values.yaml` says of migration 50 that it "is idempotent, so re-running after the fix is the safe path". No linter can check this, so the mandatory test suite carries it: every migration's suite includes a case that runs it twice against the same fixtures and asserts the second run is a no-op.

**Tests become mandatory.** Every new migration ships a bats suite driving it against fake `kubectl` fixtures, and the linter fails a migration that has none. The pattern is established: `hack/migration-50-etcd-adopt.bats` drives the real script against `hack/testdata/migration-50/` with 28 cases. This is the highest-leverage rule in the set — migration `43`'s hardcoded release name is caught by a single test case using a non-default instance name.

**A scaffold**, `hack/new-migration.sh <slug>`, generating the script and test stub with the header filled in and today's date. Conventions that require reading a document get followed unevenly; conventions the tooling hands you get followed.

### 6. Runner

`run-migrations.sh` gains a second pass, so in-flight integer migrations merge unchanged:

1. **Legacy pass** — unchanged `seq CURRENT (TARGET-1)` over `migrations/[0-9]+`, gated on `version`. Frozen: CI rejects any *new* integer file.
2. **Slug pass** — a single walk over the manifest, in batch-file order:

```sh
for batch in order.d/*; do                       # zero-padded, so plain glob order is correct
  while read -r slug flag; do
    ledger_has   "$slug" && continue             # already terminal on this cluster
    is_revoked   "$slug" && { record "$slug" revoked-unless-present; continue; }
    [ "$flag" = retired ] && continue            # deleted below the floor (§11)
    script=$(find_script "$slug") || fail "$slug is in $batch but not in this image"
    run_migration "$script" && record "$slug" ok
  done < "$batch"
done
```

There is no sorting, no dependency resolution and no version comparison at runtime: §3 settled all three at the release cut. That is what keeps the runner to a flat walk over a file, and it is why `requires` never has to be parsed here.

Two conditions are fatal and must stay fatal. A slug present on disk but in no batch means the release step did not run, and a slug in a batch but absent from the image means the packaging is wrong; both abort before anything executes. Downgrading either to a warning reintroduces the silent skip.

Legacy runs first. `background/` is not executed by the hook at all.

### 7. Execution

**Pre-apply** stays the render-gated hook. The gate generalises from a scalar compare to a set difference: the chart already ships the whole migrations directory (there is no `.helmignore` in `packages/core/platform`), so `.Files.Glob "images/migrations/migrations/order.d/*"` yields the manifest at render, and the slug list falls out of it with no directory walk and no script reads. The Job is only created when the difference is non-empty. `templates/sources.yaml` already reads chart files this way, so the idiom is established here.

**A fresh install records, it does not run.** Today a new cluster runs zero migrations, and that is two behaviours acting together rather than one rule: `templates/cozystack-version.yaml` stamps `targetVersion` straight into the ConfigMap when `lookup` finds none, and `templates/migration-hook.yaml` computes `$shouldRunMigrationHook` only inside `{{- if $configMap }}`, so with no ConfigMap the hook does not render at all. (The bootstrap branch in `run-migrations.sh` is unreachable through Helm for the same reason.) A set difference has no equivalent of that: an absent ConfigMap is an empty ledger, so `pending` would be the entire `pre-apply/` set and the first slug migration to land would change what a fresh install does.

So the install-time branch of `templates/cozystack-version.yaml` seeds the ledger, recording every slug in the manifest — both tiers — with outcome `skipped-fresh-install`, and setting `complete-through` to the newest batch. The manifest is already read at render for the gate, so this is one loop on a path taken exactly once in a cluster's life. This belongs to Phase 1 rather than Phase 5: without it Phase 1 is not behaviour-preserving, and the seeding in §10 does not cover it — that condition tests for a `version` scalar with no `m.*` keys, and it seeds from `legacy-map`, which holds no slug migrations.

**Background** is orchestrated — not executed — by cozystack-operator, because the scripts live in the migrations image and the operator has neither them nor a Helm renderer. The chart therefore writes down the two things the operator cannot derive: which slugs are background and in what order, and which migrations image the current platform release pins.

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: cozystack-migrations-index
  namespace: cozy-system
data:
  image: ghcr.io/cozystack/cozystack/platform-migrations:v1.7.0@sha256:…
  background: |                                # manifest order, filtered to the background tier
    clickhouse-keeper-pvc-labels
    tenant-ancestor-labels
```

The operator reconciles `pending = background − ledger − revoked`, walks it in the listed order, creates one Job per pending slug from `image` with `ONLY=<slug>`, and patches the ledger on success. Because the ConfigMap is re-rendered on every platform upgrade, the image ref and the list cannot drift from the release that shipped them. The operator is already `cluster-admin`, so this needs no RBAC change.

Background Jobs run **serially**, one at a time in ID order: it matches the current model, keeps failure attribution unambiguous, and avoids two migrations touching the same objects concurrently.

Two existing patterns carry over to the reconciler: the `Ready`-condition and per-failure-`Reason` vocabulary in `internal/operator/package_reconciler.go`, and the tolerant annotation-bookkeeping idiom in `internal/operator/packagesource_reconciler.go` (`readRecoveryTracking` / `clearRecoveryTracking`), where malformed state degrades to "no prior attempt" instead of wedging the controller.

Moving backfills off the blocking path also fixes a live operational problem: the migration hook carries `helm.sh/hook-delete-policy: before-hook-creation`, so under Flux's retry loop a failing migration destroys its own logs on the next attempt.

**A recorded failure is never retried automatically.** A migration that fails under `on-error=warn`, in either tier, is recorded `failed` with its reason, message, image and attempt count, and is not attempted again on the next upgrade or the next reconcile. This is the current behaviour made explicit rather than a new restriction, and the reason to keep it is that automatically retrying work nobody is watching is how a failure becomes permanent background noise. The upgrade proceeds; the record is the report.

`on-error=abort` is the deliberate exception, and it is an exception because nothing is recorded rather than because a policy says so. The Job fails, the ledger is untouched, the upgrade does not proceed — and the surrounding machinery does retry: the hook Job carries `backoffLimit: 3`, and Flux re-reconciles the failed HelmRelease and creates the hook again. The migration is still pending on every one of those attempts, so it runs again. That is correct for a blocking migration, which must succeed before the upgrade can continue, and it is why the abort path needs no retry policy of its own.

**Re-running one migration by hand.** Re-running is an operator action, and it needs no new mechanism: it is the mechanism the operator itself already uses. A one-off Job from the pinned migrations image with `ONLY=<slug>` runs exactly that migration. In `ONLY` mode the runner deliberately skips the "already recorded" check for the named slug — that is the entire point — executes it, and rewrites the entry with a fresh outcome, the image it ran under, and `attempts` incremented. It still refuses a slug listed in `revoked`, since revocation outranks any operator request, and it still refuses a slug absent from the image.

```sh
kubectl -n cozy-system create job migration-rerun-<slug> \
  --image=$(kubectl -n cozy-system get cm cozystack-migrations-index -o jsonpath='{.data.image}') \
  -- run-migrations.sh
# with ONLY=<slug> in the pod env and the operator's ServiceAccount, which is already cluster-admin
```

That keeps one execution path rather than two, needs no hand-editing of a JSON value inside a ConfigMap, and cannot corrupt the record it is meant to act on. Because §5 requires every migration to be safe to run twice, it is safe for any of them rather than for a blessed subset.

**When an operator would do this.** A `failed` entry means the work did not happen and whatever it was fixing is still broken. The record says which migration, why it failed and what it said, so the decision is: remove the cause — a tenant that was mid-delete, a throttling apiserver, an object something else held — and re-run. There is no value in re-running before that, which is the other half of why nothing retries on its own.

Failures need a surface, since nothing chases them. For the background tier the operator already reports one, as a `Ready=False` condition with a per-failure reason. For the blocking tier the ledger is the surface: a `failed` entry stays in the ConfigMap, uncompacted at any age, and `kubectl get configmap -n cozy-system cozystack-version -o yaml` is the report an operator reads.

### 8. Revocation

A migration file is immutable once it reaches any branch a release is cut from — editing `43` in place is what created the `53` situation. A faulty migration has two populations to serve, and both are handled explicitly.

**Clusters that have not run it yet** must never run it. Its slug goes in `migrations/revoked`, and the runner subtracts that set from pending. It records `m.<slug>: "… revoked"` **only when no key for that slug exists**, so the ledger stays a complete account of what was decided without overwriting what executed. The condition is the whole point: on a cluster that already ran the migration successfully, an unconditional write replaces `ok` with `revoked` and destroys the one fact an operator needs during the incident, which is whether this cluster executed it.

**Clusters that already ran it** need repair, which is a new migration with a new ID handling both the never-ran and the ran-broken states.

```
# redis-failover-group-label stamped the wrong operator-group value.
# Superseded by redis-failover-group-fix.
redis-failover-group-label
```

This turns a silent, dangerous edit into an explicit, reviewable one, and closes the never-ran population automatically rather than by remembering to fold the fix back into the original file.

**Companion CI guard:** once a migration file exists on any branch releases are cut from, its content may not change (diff against merge-base). Scoping the guard to `main` alone would miss the [#3534](https://github.com/cozystack/cozystack/issues/3534) shape precisely: one slug carrying different content on a release branch is the same silent skip in a new spelling, and the guard has to run wherever the divergence can be introduced. The ledger checksum still catches it after the fact on a live cluster, but §11 argues that detection which can be ignored reproduces the failure it is meant to prevent, and that argument applies here too. Revocation is the sanctioned escape hatch, so the guard has somewhere to point.

### 9. Why migrations stay platform-level

An earlier revision of this proposal moved migrations that only touch one package's objects into that package, delivered by that package's own pre-upgrade hook, with ledger keys namespaced as `m.<package>.<slug>`. That does not work, and the reason is the incident this document opens with.

A namespaced key has a package dimension and no instance dimension. `SeaweedFS` is a user-creatable kind: instance `foo` is owned by release `foo-system`, and a cluster can hold any number of them. One key `m.seaweedfs.db-adopt` covering N instances means the first release to run writes it and every other instance is skipped as already applied — the `43` failure again, now with a ledger entry on top asserting it succeeded. Adding an instance dimension to the key does close that hole, but it makes the ledger unbounded in the tenant's dimension rather than the platform's, and it asks a hook that can only see its own release to reason about a fan-out it cannot enumerate.

The pattern that works is already in the tree: a platform-level migration that scans the fleet, which is what `lib/seaweedfs-db-adopt.sh` does. What `lib/` was missing is not a scoping mechanism but a helper for that iteration, including the errexit handling that file documents — the guard migration `43` lacked. §5 adds it.

So migrations stay platform-level. Ownership of the objects being touched is still the right question to ask about a migration; it just does not translate into a delivery location, and three classes make that concrete:

- **Cross-package.** `26` moves resources between `extra/monitoring` and `system/monitoring`; `20` applies five packages in sequence. Neither has a single owning release.
- **Must precede a *different* package's operator.** `54` had to label every RedisFailover before `cozystack.redis-operator` upgraded. A hook on the redis *app* package fires too late, since those releases `dependsOn` the operator. This class looks package-scoped and is not.
- **Must precede the platform's own artifacts**, which is where the runner already sits.

Against those, the genuinely package-scoped set is small — `52` deletes a linstor-scheduler Deployment, `22` and `27` move CRDs into a `*-crds` release — and none of them is served worse by running from the platform hook. Scoping bought a cleaner ownership story and cost correctness on the one kind of migration that most needs it.

### 10. Converting the legacy set

The dual-mode runner is a transition device, not an end state. Leaving fifty-six integers in place forever means the runner keeps two code paths, `targetVersion` never goes away, and every new contributor has to learn a scheme that is already deprecated.

The conversion is itself a migration of the migrations, and it is mechanical:

1. Rename `migrations/1..56` to slugs describing what each does, so `1` becomes `mariadb-operator-secrets`. Order is not encoded in the names and does not need to be: step 1b writes `order.d/00000-legacy` listing all fifty-six slugs in their original integer order, which is by construction the order every existing cluster already ran them in. That batch sorts before every release batch, so the converted set keeps its position in the global sequence exactly.
2. Commit the integer → slug mapping as `migrations/legacy-map`, one `N <slug>` pair per line. It is the seeding input for step 3 and the only place the old numbering survives.
3. On first run against a cluster that has a `version` scalar but no `m.*` keys, the runner seeds the ledger from the map: every integer below `version` gets its `m.<slug>` recorded with outcome `legacy` (no checksum — those files were not immutable when they ran). Idempotent, since the seeding condition is the absence of `m.*` keys. A fresh install never reaches this path: §7 seeds its ledger at install time, so the `m.*` keys are already there.
4. Delete the legacy pass, `targetVersion`, and `hack/check-migrations-target.sh`.

Only step 3 touches clusters, and it writes ledger keys rather than running anything. A cluster stamped `57` ends up with fifty-six `legacy` records and behaves identically.

### 11. Retention

Today every migration ever written ships in the image forever, because a dense sequence cannot have holes: deleting file `7` breaks the `seq` loop that walks past it. Under a set model there is no sequence to break, so migrations can be deleted.

What makes deletion safe is declaring a floor — the oldest platform version this release accepts as an upgrade source, which is already a supported-versions policy decision rather than a new one. Migrations that only ever applied to clusters below the floor are deleted; the runner compares the cluster's ledger against the floor and **refuses**, rather than warning, on a cluster beneath it, pointing at a staged upgrade through an intermediate release. Refusing is the only defensible choice: a warning that is ignored produces exactly the silent-skip failure this proposal exists to eliminate.

Deleting a migration means deleting the script and flagging its manifest line `retired`, in the same commit:

```
# batch 00001, sealed at v1.6.0
mariadb-operator-secrets    retired
flux-tenants-cleanup        retired
```

The line stays. It costs about forty bytes and it is what lets the runner tell a retired migration from a slug it has never heard of — the second of which is a packaging error that must abort. The invariant CI enforces is one line long: **every manifest entry satisfies exactly one of {the script exists, the entry is flagged `retired`}**, and `retired` is never reversible. That is what makes it impossible for the watermark to cover something that still ships, without relying on anyone comparing dates.

The floor is also where the ledger compacts. Leaving an `m.<slug>` key behind for every migration ever run would make the ConfigMap grow monotonically for the life of the cluster, so the same operation advances `complete-through` (§4) and removes the keys that watermark now covers. Only `ok` and `legacy` records compact. A `warn` failure or a `revoked` decision keeps its own key at any age, because a hole in the history is exactly the thing worth reading.

### 12. What this retires

`hack/check-migrations-target.sh` and `targetVersion`, the cross-branch slot-alignment check proposed in [#3534](https://github.com/cozystack/cozystack/issues/3534), and the question of which PR owns a number. Backports become safe by construction: a slug applied on `release-1.5` is recorded under that slug, and the 1.6 upgrade skips it and runs its own.

## User-facing changes

**Cluster administrators.** No change to the upgrade command or its behaviour. The `cozystack-version` ConfigMap gains one key per applied migration, so `kubectl get configmap -n cozy-system cozystack-version -o yaml` becomes a readable account of what ran and when — today it shows a bare integer. Background migrations become visible as Jobs in `cozy-system` and as conditions on the operator, where today they are invisible steps inside one blocking hook.

**Tenants.** None. No tenant-facing API, CR shape or dashboard surface changes.

**Contributors.** The authoring workflow changes materially and is the main documentation deliverable: `hack/new-migration.sh <slug>` scaffolds the script and its test, the header block is mandatory, a bats suite is mandatory, and the linter runs in `make unit-tests`. `docs/agents/contributing.md` gains a migrations section; the "pick the next number and bump `targetVersion`" instruction is removed wherever it appears.

## Upgrade and rollback compatibility

**Existing clusters.** The ledger seeds from the existing `version` scalar, so a cluster stamped `57` is treated as having applied legacy migrations `1..56` and nothing else. No backfill migration is required — the seeding is runner logic, not a migration, which matters because migrations are the thing being refactored.

**Fresh installs.** The ledger is seeded at install time with every shipped ID recorded as `skipped-fresh-install` (§7), so a new cluster runs no migration and starts no pod, exactly as today.

**Phase 1 is behaviour-preserving.** Every migration is `pre-apply`, the legacy pass is untouched, and fresh install is seeded rather than run, so a cluster upgrading or installing through Phase 1 does exactly what it does today.

**Rollback.** Rolling the platform back to an earlier release leaves the ledger keys in place. A rolled-back release simply does not know some IDs, so it computes a smaller pending set and skips them; on rolling forward again they are already recorded and are not re-run. Ledger keys are additive and never removed on downgrade.

This is clearer than the scalar rather than safer, and the difference is worth stating because it is easy to overstate. Nothing lowers `version` today: the ConfigMap carries `helm.sh/resource-policy: keep`, its template is guarded by `{{- if not $configMap }}` so it never re-renders once it exists, and after a rollback the hook gate is false anyway because `targetVersion` is now lower than `currentVersion`. Today's rollback runs nothing. What changes is that the new model says so on the object instead of leaving it to be re-derived from an inequality.

**Irreversibility.** Revocation is not a rollback: it prevents a migration from running on clusters that have not yet run it, and does nothing to clusters that have. Repair of the latter always requires a new migration. This is flagged explicitly because it is the property most likely to be misread under incident pressure.

**Phase 5** deletes `targetVersion`, which is a breaking change for anything reading it — see Testing for the upgrade-lane assertion that must be replaced first.

## Security

No new trust boundary. The migration hook already runs with `cluster-admin` via a per-hook ServiceAccount and ClusterRoleBinding created and deleted around the Job, and cozystack-operator is already bound to `cluster-admin`; the background tier reuses the operator's existing identity rather than introducing another privileged principal. Migrations are, and remain, arbitrary root-equivalent code shipped inside a digest-pinned image.

The `cozystack-version` ConfigMap carries `platform.cozystack.io/no-delete: "true"` and is guarded by the deletion-protection ValidatingAdmissionPolicy; the added `m.*` keys inherit that protection. No new tenant-supplied input reaches any migration, no new secrets are stored or transmitted, and no new RBAC surface is created.

One property improves: the ledger records a sha256 of each script as it ran, so a migration whose content changed after being applied is detectable on a live cluster rather than only by reading git history.

## Failure and edge cases

- Fresh install → the ledger is seeded with every shipped ID as `skipped-fresh-install`, no Job is created, and nothing executes.
- Migration file missing from the image but pending → runner exits non-zero and refuses to advance, preserving today's behaviour. A missing file indicates a packaging mistake and must fail loudly rather than stall the cluster at a stale state.
- Migration already recorded in the ledger → skipped, not re-run, on every subsequent upgrade.
- Revoked ID, never applied here → skipped, and recorded as `revoked` so the ledger stays complete.
- Revoked ID already applied here → skipped, and the existing `ok` record is left alone. Revocation never rewrites history.
- `on-error=abort` migration fails → the hook Job fails, the platform upgrade does not proceed, and nothing is recorded for that ID.
- `on-error=warn` migration fails → logged, upgrade proceeds, and the slug is recorded with a non-`ok` outcome so it is visible rather than indistinguishable from success.
- Applied migration's file content changed since it ran → checksum mismatch reported. The migration is not re-run; repair is a new ID.
- `requires` names a slug that does not exist, names one in a later batch, or the graph has a cycle → the **release cut** fails. The runner never evaluates `requires`, because §3 resolved it into the manifest.
- Slug on disk but in no batch → the release step did not run for this build. Runner aborts before executing anything.
- Slug in a batch, not flagged `retired`, and absent from the image → packaging error. Runner aborts.
- Two migrations sealed in the same batch with no `requires` → both run in slug order, identically on every cluster, because every cluster sees that batch in one pass off one image.
- Cluster with `version` set and no `m.*` keys (Phase 5) → seeded from `legacy-map`; running twice seeds nothing further, since the condition is the absence of `m.*` keys.
- Background Job fails → recorded `failed` with reason and message, surfaced as a `Ready=False` condition with a distinct reason on the operator, and **not** retried on the next reconcile. The upgrade is not blocked.
- Operator re-runs a failed migration → a one-off Job from the pinned image with `ONLY=<slug>`; the runner bypasses the already-recorded check for that slug only, and rewrites the entry with a fresh outcome and `attempts` incremented.
- `ONLY=<slug>` names a revoked or absent slug → refused. Revocation outranks an operator request, and an absent slug is a packaging error either way.
- Cluster below the retention floor (Phase 5+) → runner refuses and names the intermediate release to upgrade through.

## Testing

Test-first throughout. The runner is a shell script driven against fake `kubectl` fixtures, a format already established by `hack/migration-50-etcd-adopt.bats` (28 cases against `hack/testdata/migration-50/`) and `hack/migration-seaweedfs-db-adopt.bats`.

**Unit (bats, `hack/cozytest.sh`).** Runner: every case in *Failure and edge cases* above. Ordering: the runner walks batches in filename order and entries in listed order; a slug on disk and in no batch aborts; a slug in a batch and absent from the image aborts; a `retired` entry is skipped without a script. Seal step: a new batch appends only, `requires` reorders within a batch, a `requires` naming a later batch fails, the head of the new manifest equals the previous release's byte for byte. Ledger durability: `stamp_cozystack_version` after `record_migration` must not prune `m.*` keys, and a `revoked` record must not overwrite an existing `ok`. Idempotence: every migration's own suite runs it twice against the same fixtures and asserts the second run changes nothing. Compaction: advancing `complete-through` removes `ok` and `legacy` keys it covers and leaves `warn` and `revoked` records standing; a manifest entry that is neither present on disk nor `retired` is rejected. Linter: missing or unparseable header, undeclared `tier`/`on-error`, `#!/bin/bash`, a ledger write from inside a migration, a migration with no bats suite, a dangling `requires` — each rejected.

**Unit (helm-unittest).** Render gate: nothing pending → zero documents; legacy-only pending; ID-only pending; both. Fresh install (no ConfigMap in `lookup`) → the seeded ledger renders, carrying one `skipped-fresh-install` record per shipped ID, and no Job renders at all. The existing `packages/core/platform/tests/migration_hook_skip_backup_test.yaml` asserts the hook's env by positional index, so it is rewritten alongside.

**Controller (envtest).** Pending computation including revoked, Job creation, ledger patch on success, a failing Job surfacing as `Ready=False` with a distinct reason and **not** being recreated on the next reconcile, and an `ONLY=<slug>` run bypassing the already-recorded check for that slug alone while still refusing a revoked one.

**E2E (upgrade lane).** [#3276](https://github.com/cozystack/cozystack/pull/3276) currently asserts the stamp reached `migrations.targetVersion`. Its replacement, required before Phase 5 retires `targetVersion`, is an **empty-pending assertion**: every slug in the manifest of the tree under test that is not flagged `retired` must have a matching `m.<slug>` ledger key whose outcome is terminal — `ok`, `legacy`, `revoked` or `skipped-fresh-install`. Because a fresh install seeds every slug it ships, the lane must additionally assert that the slugs introduced between the base release and the release under test carry `ok` specifically, or it passes trivially on a clean install. Both sides are cheap — `cat order.d/*` in the checked-out tree, read the ConfigMap's keys — and it is strictly stronger than the integer compare, because it proves each individual migration reached a terminal state rather than that a counter moved. The background half is asynchronous, so it polls; any other outcome fails the lane and names the offending ID.

Existing suites that pin the current shape and are rewritten rather than ported: `hack/cozystack-version-stamp.bats` (its architectural guard hardcodes the `>= 42` numeric-filename convention; it becomes a linter rule), and the literal next-version assertions (`51`, `54`) inside `hack/migration-50-etcd-adopt.bats` and `hack/migration-seaweedfs-db-adopt.bats`, which disappear with migration-side stamping. `hack/migration-seaweedfs-db-adopt.bats` also `sed`s `FROM alpine:` out of the Dockerfile, so that line's shape must be preserved.

[#3458](https://github.com/cozystack/cozystack/pull/3458) adds a second ordered runner (`preflight/`) to the same image at hook weight 0. It is a co-tenant and is not disturbed by this work.

## Rollout

Each phase is independently shippable.

**Phase 1 — engine and contract.** Dual-mode runner, `record_migration`, set-difference render gate, header parser and `on-error` handling, `hack/lint-migrations.sh`, `hack/new-migration.sh`, `lib/` helpers including the fleet-iteration one, fresh-install ledger seeding (§7), `hack/seal-migration-batch.sh` wired into the release cut, and five CI guards (integer freeze, content immutability on every release branch, slug grammar and uniqueness, `order.d/` add-only and release-job-only, and the prefix check at the cut). Every migration is `pre-apply`, so runtime behaviour is unchanged. The linter enforces on `pre-apply/` and `background/` only; the 56 frozen integers are grandfathered.

**Phase 2 — background tier.** Index ConfigMap template and the operator reconciler. Applies to new work only; the legacy integers stay frozen where they are, since `44`, `48`, `49` and `51` are already applied essentially everywhere.

**Phase 3 — withdrawn.** Package scoping was the original Phase 3. §9 records why it is dropped: a package-namespaced ledger key cannot address the individual instances of a user-creatable kind, which is the `43` failure over again. The number is kept rather than reused so that Phase 4 and Phase 5, referenced throughout this document and in its review, keep meaning what they meant.

**Phase 4 — attrition.** Reduces the rate at which new migrations are needed; retires none of the existing ones. *Admission-time defaulting* for fields on objects Cozystack does not template: migration `51` exists because vm-operator stamps `volumeClaimTemplate` labels only onto PVCs created after the chart change, and the StatefulSet controller never re-labels an existing PVC — Cozystack cannot template those PVCs, a controller creates them. A `MutatingAdmissionPolicy` matching `CREATE` on `persistentvolumeclaims` owned by a cozystack-managed VMCluster stamps the label at creation. It must be capability-gated exactly as `packages/system/cozystack-basics/templates/ingress-hostname-policy.yaml` gates its VAP on `.Capabilities.APIVersions.Has`, because the management cluster floor is 1.33 while `MutatingAdmissionPolicy` is beta in 1.34 and GA in 1.36. It is prevention only — existing PVCs still need the one-shot backfill. *API-layer conversion* for the values-format class: migration `39` rewrites flat `resourcesPreset` names into instance-type names across every App CR, and `pkg/apis/apps/presets/legacy.go` records that the same table is mirrored in four places. Accepting the legacy spelling in the aggregated API's conversion and defaulting layer means the stored CR never has to be rewritten and roughly fifteen migrations of this shape stop existing. Each half warrants its own proposal.

**Phase 5 — legacy conversion and retention.** Commit `legacy-map`, implement seeding, delete the legacy pass and `targetVersion`. Rename the set to slugs and write `order.d/00000-legacy`. Depends only on Phase 1 plus at least one shipped release of soak, so the slug path is exercised before the seeding path is added; independent of Phases 2–4. The retention floor is introduced in the first release that actually deletes a migration, not before.

## Open questions

- §9 keeps every migration platform-level, which is right while everything ships from one repository. If packages genuinely split out, what unit owns a ledger is open — plausibly the bundle rather than the package, since a bundle has cross-package migrations by construction and a single package does not.

## Alternatives considered

**Data model — a CRD ledger with operator status.** Rejected for the two reasons in §4: `lookup` against a not-yet-Established CRD fails silently rather than loudly, and `hack/update-codegen.sh`'s catch-all `mv` plus the hardcoded CRD lists in `internal/crdinstall/install_test.go` and `hack/e2e-install-cozystack.bats` make adding a kind to `cozystack.io` a non-local change with downstream consumers. Only the first of those is conditional: the `lookup` hazard exists because a Helm render gate reads the state, so a design that removes the render gate entirely removes the hazard with it. The second cost is unconditional and, as of this writing, unsized.

**Data model — keep the scalar, add a cross-branch CI check.** This is the mitigation [#3534](https://github.com/cozystack/cozystack/issues/3534) itself proposes. Rejected as the primary fix: it detects divergence rather than preventing it, requires every supported branch to be checked out at review time, and leaves slot contention and the `targetVersion` lint untouched.

**Runtime — per-package Helm hooks with no ledger.** Rejected: a hook fires on every upgrade of its release, so one-shot work needs its own state anyway; there is no record of what ran or whether it succeeded; and there is no ordering across charts, which the cross-package migrations require. An earlier revision adopted them as a *delivery* mechanism on top of the ledger; that is withdrawn for an independent reason (§9).

**Runtime — operator-driven execution for everything.** It does not lose the runs-before-chart-apply guarantee, which is the reason usually given and is wrong: `migration-hook.yaml` is a `pre-upgrade,pre-install` hook on the platform chart, the same chart renders the `Package` CRs, and `internal/operator/package_reconciler.go` builds the HelmReleases from them — so the hook already sits ahead of every package rollout, and the §2 class that must precede a *different* package's operator is covered today. A per-package gate in the reconciler is finer-grained, which buys less blocking rather than more ordering.

Two things do argue for keeping pre-apply on the hook for now, and a proposal moving it has to answer them first. The controller would need the incoming release's migrations *before* it rolls the bundle that carries them, which the hook gets free from the render; reading the artifact through source-controller, which fetches ahead of any rollout, is the shape to prove there. And the CRD costs in §4 stay unsized. One argument for the move survives both, and this proposal cannot answer it: a ledger entry can only say "done", while some work is an invariant that must hold across several upgrades — `lib/seaweedfs-db-adopt.sh` documents exactly that, noting its `keep` window "is not a one-shot". A controller can hold an invariant. That deserves its own proposal.

**Tiering — a third, *convergent* tier.** Some migrations are a predicate over current state rather than a one-shot edit: the label backfills (`48`, `49`, `51`) and the defensive cleanup (`44`). Declaring that would let them skip the manifest and the ledger and simply run on every upgrade, the way Flyway's repeatable migrations do — which is why `dpkg` and `rpm` survive version skips so calmly, their conditions being declarative rather than historical. Rejected because the saving is nil and the cost is not: it is four migrations of fifty-six, a manifest line is forty bytes and the walk is one pass either way, against a fleet-wide sweep on every upgrade forever for work already done. The distinction is also carried already by the two axes that exist — `tier` says whether a migration blocks, `on-error` says what its failure means — and every example in the tree has all three agreeing. The one thing it would genuinely have added, *safe to re-run*, is instead a contract rule in §5 binding every migration, because manual re-runs (§7) need that to hold everywhere rather than in a declared subset.

**Ordering — a date or sequence in the filename.** Rejected by §3: an authoring-time key is independent of which release first ships the migration, so it agrees with the required order only by luck. The variant that repairs it — a CI rule refusing an ID older than the newest already merged — was rejected separately for its running cost, since it forces a rename on every stale rebase and stale rebases are common. A two-digit intra-day sequence fails for a sharper reason: the author picks it before knowing which migrations land in the same release, so it can express neither intra-day nor intra-release order.

**Ordering — a declared dependency graph as the order.** Rejected, with the worked reason in §3. Django is the honest version of this design: it disclaims filename ordering outright and never promises a stable order for pairs with no declared edge. `requires` is kept here, but as an assertion resolved at the release cut rather than as the ordering mechanism.

**Ordering — the release step renames files, `goose fix` style.** goose renames timestamped development files to sequential integers in CI once they are ready to ship. Rejected because the filename is the ledger key here, so a rename would rewrite cluster state and break cross-branch identity. A manifest beside the files gets the same effect without touching the name.

**Ordering — a batch stamped into each script's header at release time.** Tempting because a cherry-pick then carries the assignment with the file. Rejected: a migration that reaches `release-1.6` before `main` cuts its next release is stamped on the branch while `main`'s copy is not, so the same slug ends up with two batches — and unlike a central manifest there is no single artifact where the conflict can be detected. It also weakens immutability from "once merged" to "once released" for no gain.

**Ordering — forbid the skip, as kubeadm does.** `MaximumAllowedMinorVersionUpgradeSkew = 1` with no bypass flag, and Cluster API defers to it; Flyway's `outOfOrder=false` is the same posture. Requiring 1.6 → 1.7 → 1.8 would make the pending set exactly one release's worth on every cluster, satisfying the ordering requirement with no machinery at all. Rejected as a product decision rather than a technical one: the cost lands on air-gapped and slow-moving clusters, which are a real part of the Cozystack population, and it is very hard to relax later once the ordering machinery has been left unbuilt. It also does not close patch-level skips within a line, which is exactly where backports live.

**Runtime — Kyverno `mutateExisting` for the backfill class.** Rejected: it adds a dependency, and its existing-resource mutation is explicitly asynchronous with variable delay — disqualifying wherever the mutation must land before a Helm prune.

**Schema — `StorageVersionMigration` (`storagemigration.k8s.io`).** Not applicable: still alpha/beta in the Kubernetes versions Cozystack supports, and App objects are served by an aggregated API server rather than stored as CRDs.

**Language — rewriting migrations in Go.** Rejected: the operations are `kubectl` calls, and shell keeps them reviewable and testable against fake-binary fixtures without a build step in the image. The problem is the absence of a contract, not the language.
