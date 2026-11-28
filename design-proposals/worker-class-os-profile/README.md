# WorkerClass is the only OS profile of a tenant worker pool

- **Title:** `Worker pools carry no OS settings; the OS profile lives only in WorkerClass`
- **Author(s):** `@lexfrei`

## Overview

I propose that `KubernetesNodes` stops carrying OS settings. They live only in a [WorkerClass](../kairos-tenant-worker-pools/), and a tenant who needs different settings uses a different class. Classes are immutable and fully explicit; `talos` and `kairos` become aliases that admission replaces with a platform-generated class. Platform OS settings move from [installation defaults](../application-defaults/) into that class. Existing pools move into generated classes carrying their exact current configuration; the migration itself replaces no worker. The implementation is a pull request at the bottom of the worker class stack (linked from this proposal's pull request), Talos only: the WorkerClass API, platform-generated classes, admission, the pool schema without OS fields and the migration. The Kairos pull request (cozystack/cozystack#4911) is rebased onto it and adds the `kairos` bootstrap with hoster-published classes. Generated Kairos classes from the disks package come in a separate pull request of the same stack.

## Scope and related proposals

Amends [`application-defaults`](../application-defaults/) (worker example only) and [`kairos-tenant-worker-pools`](../kairos-tenant-worker-pools/) (tenant OS overrides withdrawn; this is its "required later migration"); see the two sections below. [`kubernetes-nodes-split`](../kubernetes-nodes-split/) is amended in one passage: Phase 2, "What changes", "while preserving the classless Talos path for existing pools". Registry mirrors inside Kairos guests are out of scope. "The stack" below is the unmerged worker class stack, cozystack/cozystack#4911 and above. Main has no WorkerClass and no class path in `kubernetes-nodes`.

## Decisions

## Context

### The problem

1. On the stack, a configured installation-defaults entry can fill absent `talos.*` fields before the validating webhook, which cannot tell those values from tenant values. Explicit pool values are not overwritten. Injected Talos settings can cause a Kairos pool to be refused for fields the tenant never sent; on a custom Talos class they override the class, and settings that conflict with a fixed builtin image or Proxmox template cause the pool to be refused.
2. On the stack, a pool on the built-in `talos` class can take settings from chart policy defaults at render (`defaults.json` in `Resolve`, `files/worker-defaults.json` in `_workerclass.tpl`). Changing those defaults could roll pools that relied on them; an explicit pool override can preserve its value. Built-in `kairos` without `image` reads the shipped disk tag the same way.

## Goals

- A pool's spec contains no OS setting; its class fully determines its OS profile, and no change to a chart or platform value rolls a pool through an OS setting the class carries. The exceptions are the automatic NVIDIA module list (Design item 2) and the one-time roll of a pool classed in the upgrade window (Failure and edge cases).
- Air-gapped Talos settings are configured once, in platform values.
- Every existing pool that stores its OS values moves onto a class with unchanged template names and no new Machines. ComputePlane pools are not rolled by the migration itself and do not roll on later chart releases (Design item 6).

### Non-goals

Tenant OS overrides at any granularity. Tenant choice of class for ComputePlane pools.

Adding an OS family without a code change. The pool and the application API carry no OS settings, so a new family adds no pool field; the pool fields whose rules depend on the family, `sshKeys` and `gpus`, get rules for it in the resolver. Inside the class the families are a closed list. The Kairos family needed at least an enum value, a typed settings block in the CRD and a resolver branch; a Cluster API bootstrap provider in its own package; bootstrap and machine template branches in the pool chart, including disk import, firmware and a reconcile Job; wiring in the tenant control plane chart; family-switch rules in admission and the pool chart; an alias, a name prefix and a source of images for its generated class; restore handling (today a refusal); and e2e coverage.

## Design

### 1. Pool fields

Removed from the schema and values: `talos.*` (`version`, `schematicID`, `imageFactoryURL`, `imageFactoryCA`, `installerRepository`, `registryMirrors`, `sysctls`), `kernelModules`, `osImage`, `proxmox.templateTags`. Everything else stays. `sshKeys` stays as tenant access, with the stack's family rules: Talos refuses a nonempty list, Kairos accepts at most one key. `gpus` stays as hardware attachment. Its driver side is a class property, the stack's automatic NVIDIA module rule applies when the class leaves `kernelModules` unset, and Kairos refuses `gpus`. The schema's top level is open and the API stores removed keys, so the validating webhook enforces the rule without a change to the application API code:

- A pool is unmigrated while its spec has no `workerClassName`. CREATE without a class cannot happen.
- UPDATE of an unmigrated pool is allowed only if `talos`, `kernelModules`, `osImage` and `proxmox.templateTags` are unchanged. When the request omits them, the mutating webhook copies them from the old object in the admission request, without a read, so a client built against the new schema can still update neutral fields.
- On a pool with a class, those four keys are refused on CREATE and UPDATE, and removing the class is refused. Once a pool has a class, the unmigrated exception ends for good.
- An UPDATE that adds a class to an unmigrated pool is refused for clients; only the controller's migration classes a pool. The chart refuses the four keys on a classed pool, covering direct HelmRelease writes.

### 2. Fully explicit classes

`TalosSettings` gains `sysctls`; a tenant who needs, for example, gVisor sysctls needs a separate class. The CRD requires `talos.version`, `schematicID`, `imageFactoryURL` and `installerRepository` to be present, while the resolver validates the effective values, so a migrated class may carry an empty `talos.imageFactoryURL` for a builtin KubeVirt image or a Proxmox template, where the field is not used. `imageFactoryCA`, `registryMirrors` and `sysctls` are optional and absent means empty, never a chart default, because a chart default read at render rolls pools on chart upgrade. Absent `kernelModules` means the automatic NVIDIA rule. That rule's module list ships with the chart, so a release that changes it rolls GPU pools on classes that leave `kernelModules` unset. A Kairos class carries only `image` and `bootstrap`. A Kairos class without `image` is refused, and the pool chart does not fall back to a shipped disk tag. Resolution is the class alone plus the stack's family and fixed-contract checks; `storedDefault` and the pool `talos` merge leave `Resolve` and `_workerclass.tpl`, and the chart stops rendering from stored OS fields once no pool stores them. A classless pool without OS fields, the entry state of a ComputePlane pool, renders from chart defaults until the controller classes it.

### 3. Family aliases

On CREATE the mutating webhook replaces `talos` or `kairos`, sent or supplied by `defaultWorkerClass`, with the current platform-generated class of that family (for Kairos, of the pool's minor). The `kairos` alias is refused, with a message telling the tenant to name a WorkerClass, whenever no generated class exists for the pool's minor: before the Kairos disks ConfigMap exists, after it is removed, or for a minor it does not list. On UPDATE an alias resolves exactly as on CREATE, for the pool's possibly updated minor, and is not replaced with the stored class. Sending `talos` or `kairos` in an update is the explicit request to move the pool to the current platform class, and it rolls the pool if the class differs; resending the stored concrete name changes nothing. This is how a pool moves to a Kubernetes minor its stored class does not allow (a Kairos class pins its minor through `image`, and the stack's `kairos()` refuses a mismatch), and how a pool leaves a `legacy-` class. On a hoster class, the hoster publishes a class for the new minor and the tenant names it. A family switch is refused by the validating webhook, which reads both classes. The mutator takes generated names from the controller and reads nothing in admission. A stored pool never names an alias.

### 4. Platform-generated classes

The package controller generates them, not the chart. At start it takes `kubernetesWorkerClasses.platform.talos`, which the chart passes to it, and the resolver's pins, computes the name, creates `talos-<40hex>` in each replica before serving (an existing class with the same spec is fine; the same name with another spec is a fatal start error) and only then hands the name to the mutating webhook. While running, the leader watches WorkerClasses: it recreates its generated class if the class is deleted (an unreferenced class has no pool holding it, so a hoster can delete it), and the leader's process exits with the same fatal error if a class with that name and another spec appears. The other replicas do not depend on that watch: on its uncached GET of a generated name, the validating webhook checks the hash of the live spec against the name, independently of its version's expected map. For its own generated class it also compares the whole live spec with the expected one. The hash check covers a new mutating webhook's name reaching an older validating replica during rollout. The hash is computed in one place.

- One Talos class, `talos-<40hex>`. `kubernetesWorkerClasses.platform.talos` accepts every `TalosSettings` key. Unset `version`/`schematicID` come from the resolver's pins, unset `imageFactoryURL`/`installerRepository` from its policy defaults, optional keys empty.
- Generated Kairos classes come from a separate system package that builds the Kairos disks and has its own directory and single producer. It renders a ConfigMap mapping each supported Kubernetes minor to a digest-pinned disk and the exact Kubernetes release that disk carries, the two parts of a Kairos class `image`. The same producer owns both parts. Every replica that serves the mutating webhook reads and watches that ConfigMap through the API, so alias resolution does not lag on a non-leader. The controller accepts only digest-pinned references and generates `kairos-<minor>-<40hex>` with the same create-and-verify path as the Talos class. The minor is normalized from `v1.35` to `v1-35`, giving names such as `kairos-v1-35-<40hex>`. A changed disk gives a new class; existing pools stay on theirs. The ConfigMap lives in the platform namespace, which tenants cannot write, and the controller reads it only there. Until it exists, Kairos pools use hoster-published classes with an explicit digest-pinned `image`.
- Talos pins: the resolver embeds them from `internal/kubernetesworker/config/defaults.json`, and the chart's `files/worker-defaults.json` is a generated copy that `hack/check-worker-image-catalog-defaults.bats` checks byte-equal.
- `<40hex>`: the first 40 hex characters of SHA-256 over the canonical spec; metadata is excluded. New values or pins give a new name; existing pools stay on the old one. The prefixes `talos-`, `legacy-` and `kairos-` are reserved in full. Hoster classes use other names; CRD validation and admission refuse malformed generated names.
- `defaultWorkerClass: talos` resolves to the generated Talos class; a custom name is allowed.
- Retention: generated classes are not Helm-owned, so no Helm upgrade deletes them, and the stack's finalizer holds referenced ones. Collecting unreferenced generated classes is not part of this design (open question 2).

### 5. Migration

The package controller (`kubernetes-worker-controller`) processes every `kubernetes-nodes` HelmRelease without `workerClassName`, with no time budget:

1. Wait until the MachineDeployment carries `workerclasses.cozystack.io/renderer: "class-aware"`, which the new chart's legacy path writes into MachineDeployment metadata, the HelmRelease is Ready with `observedGeneration` equal to `generation`, and the HelmRelease has deployed the chart revision currently served by the artifact its chart reference points at. A new chart revision does not change the HelmRelease generation, so Ready alone can describe the previous revision. The revision condition is checked when planning and again on the fresh object before the patch. Patching a pool still on the previous chart would roll it.
2. Compute effective values with the standard Flux resolver, resolve the pool as classless and write a complete class spec: `talos` with `sysctls`, `kernelModules` only if the pool set a list, the KubeVirt `osImage` or Proxmox `templateTags`.
3. If the spec equals the current platform class's spec, reuse that class, after the same check of the live class's spec that the validating webhook makes. Otherwise create or reuse `legacy-<40hex>`. Wait for the class's finalizer.
4. Re-read referenced dependencies before patching; if they changed, retry. Patch the HelmRelease in one write, conditional on resourceVersion: set `workerClassName`, remove the OS fields.

The no-roll guarantee requires the operator to leave referenced values unchanged during migration. If referenced values supply OS settings or other inputs used to compute the class spec, automatic migration refuses, naming the references and keys without revealing Secret values. The operator must move the effective values inline and remove those keys from the reference. Shared service references used by ComputePlane are retained. Re-reading dependencies before patch does not make the operation atomic across objects. A change after the last check can stop the upgrade at the template guard and require operator intervention instead of completing automatically.

No roll: template names are hashes of rendered content (`nodegroup.yaml`), the class name appears only in the MachineDeployment annotation `workerclasses.cozystack.io/class`, and the class-aware chart fails the first classed render of a live MachineDeployment whose `configRef` or `infrastructureRef` would change, as the stack's chart does. New in this design, the guard applies only to a MachineDeployment that carries the `renderer` marker, and every migrated pool carries it because the controller patches only after seeing it. A `legacy-<40hex>` name taken by another spec, or a pool the resolver refuses, stays unmigrated with an event and an annotation on its HelmRelease. Other pools proceed. The platform migration hook cannot run this: it is pre-upgrade of the platform chart, before the new PackageSources bring the CRD with `talos.sysctls` and the new pool charts.

### 6. ComputePlane pools

The ComputePlane parent renders each pool's HelmRelease from an allowlist, so these pools store no OS values and render from each release's chart defaults. Rendered HelmReleases carry the Flux origin label `helm.toolkit.fluxcd.io/name`.

- A new pool starts without a class, like an existing one, renders from the chart defaults and is classed by the controller the same way: the platform class when the specs are equal (Design item 5, step 3), otherwise a `legacy-` class. No channel could give the module a generated name.
- Known limit: `kubernetesWorkerClasses.platform.talos` does not reach ComputePlane pools, as installation defaults do not reach HelmReleases a parent chart writes.
- For an existing pool, the module keeps the `workerClassName` stored in the live HelmRelease (`lookup` of its own object), or writes none if none is stored. Handing such a pool the platform class would switch it to the class path and roll it, so it stays classless until the controller migrates it. The module also writes `workerclasses.cozystack.io/renderer: "class-aware"` into the child HelmRelease metadata.
- The controller migrates a HelmRelease with the Flux origin label only when that annotation is present, in addition to the MachineDeployment marker; otherwise the parent's next upgrade could drop the class.
- The legacy class is computed from what the pool renders at migration time, the new chart's defaults; template names do not change at migration.
- Guarantee: the migration itself does not roll ComputePlane pools. The release that ships this change renders them from its chart defaults, as every release does, and rolls them only if those defaults changed. After migration their class is fixed and later chart releases do not roll them.

### 7. Deprecated `talos` entry

Left alone, an `applicationDefaults.kubernetesnodeses` entry with `talos.*` fails schema validation and the API skips the whole entry, neutral keys included. The platform chart leaves `talos` out of the rendered defaults and uses it as `kubernetesWorkerClasses.platform.talos` when that is unset; both set and different fails the render, naming the key to remove. `kernelModules`, `osImage` and `proxmox.templateTags` in the entry fail the render, pointing at a WorkerClass and `defaultWorkerClass`. Other keys keep working. Platform values are never rewritten; installation defaults code is unchanged.

## What this changes in application-defaults

Superseded passages:

- Configuration, the `kubernetesnodeses.talos` block; "Example: tenant Talos workers", all three paragraphs (settings move to `kubernetesWorkerClasses.platform.talos`).
- Merge rules, the worker-pool sentences "such as `osImage.builtin` and `osImage.factory` of a worker pool" and the one about a pool's map of registry mirrors, and merge cases 15 to 18 and 22.
- Upgrade and rollback compatibility, "No migration rewrites existing HelmRelease values" (for `KubernetesNodes`); Testing, the KubernetesNodes render tests of stored factory URLs, installer repositories and mirrors.

## What this changes in kairos-tenant-worker-pools

Superseded passages:

- Title, "and tenant OS settings"; Overview, third paragraph (tenant overrides); Goals, "Preserve supported tenant Talos choices".
- §1, built-ins that "resolve to the platform's baseline"; the factory source resolved "from supported pool overrides". §3, pool overrides of `talos.*`, `kernelModules`, `osImage`, `proxmox.templateTags` in the resolution order and the pool-choice table. §2 table, "UPDATE selecting another class" row ("OS-specific overrides").
- §4, the overriding Talos example, adoption by UPDATE, ComputePlane pools kept classless.
- User-facing changes, "baseline plus the supported tenant overrides"; Upgrade, "Already-classless legacy pools need no restoration", "Existing legacy pools may remain legacy through subsequent upgrades", "preserve tenant overrides" and "not a ban on these field inputs"; Security, "Tenant overrides are limited to supported pool settings".
- Testing, render and API contracts: "Classified Talos baseline plus tenant overrides...", "partial Talos overrides", "ComputePlane renders its existing legacy Talos shape with no class set". Rollout step 4, "supported tenant configuration". Rollout step 3: classified Talos baselines/overrides, the unchanged ComputePlane boundary, and "do not enable a compulsory platform migration in non-ISP variants" (here the controller migrates wherever the package is installed).
- Alternatives, "Hoster owns every Talos choice" is now the design. Open questions 12, 13, 14, 15, 16 are answered by Design items 4 and 6, rollback, and Design item 5.

## Requirements this design changes

Four requirements from the sibling proposals change. No roll when a platform default changes (application-defaults) now holds through new class names only, and for ComputePlane pools from their migration on (Design item 6). Precedence of pool over class over platform (kairos-tenant-worker-pools) gives up the pool tier deliberately, because the class is the whole OS profile. Legacy pools render unchanged (kairos-tenant-worker-pools) through identical template names at migration, ComputePlane pools as in Design item 6. Air-gapped Talos and Kairos pools without per-pool repetition (cozystack/cozystack#3950 and kairos-tenant-worker-pools) are served by `kubernetesWorkerClasses.platform.talos` and Kairos class `image` references, either hoster-published or generated from the disks ConfigMap; Kairos in-guest mirrors stay out of scope.

## User-facing changes

A pool shows a concrete class name such as `talos-<40hex>` even when the request named `talos` or nothing. Updating a pool with `workerClassName: talos`, or `kairos` when a platform Kairos class exists for the pool's minor, moves it to the current platform class, which rolls it if the class differs. A client that sends the alias back on every save therefore moves the pool whenever the platform class changes; clients that edit pools should send back the stored concrete name.

## Upgrade and rollback compatibility

On upgrade the platform chart converts the deprecated entry (Design item 7) and the controller migrates pools as they reach the new chart (Design items 5 and 6). A later release removes rendering from stored OS fields and refuses to upgrade while a pool that stores OS fields exists. A classless pool without OS fields, rendering from chart defaults, stays as the entry state of ComputePlane pools, and the controller keeps classing them. Before a rollback, remove Kairos pools and run the stack's restore tool (`Prepare` in `internal/kubernetesworker/restore/restore.go`), which writes each classed pool's effective Talos settings back into the old fields. For a `legacy-` class this reproduces the pre-migration effective configuration.

Restoration also holds automatic migration. With `--apply`, the restore tool uses one write conditional on `resourceVersion` to set the metadata annotation `workerclasses.cozystack.io/migration-hold: restored-for-rollback`, restore OS values and remove `workerClassName`. The controller skips a held HelmRelease and checks the hold again on a fresh object before patching. A stale migration patch conflicts with the restore write, and its retry sees the hold. The annotation is not an OS value and does not change templates. The operator must explicitly remove it if the rollback is cancelled and before a subsequent upgrade; upgrading does not remove it automatically.

## Security

The validating webhook refuses a CREATE naming a `legacy-` class, and a class change to one unless it is already the stored class, since a legacy class may carry a tenant's reachability settings. The controller gains `create` on WorkerClasses, for the platform class and the legacy classes, `patch` on `kubernetes-nodes` HelmReleases, `get`, `list` and `watch` on MachineDeployments and on the Kairos disks ConfigMap in the platform namespace, and `create` on events.

## Failure and edge cases

- `legacy-` name collision or unresolvable pool: see Design item 5. An interrupted migration leaves an unreferenced `legacy-` class that the next pass reuses.
- A hoster class named like a generated class with another spec makes the new controller refuse to start, with a message telling the hoster to delete or rename that class. The webhooks are served by the same process, and the validating webhook refuses a generated name whose live spec is foreign, so no pool is admitted on the foreign class, and the migration does not run until the hoster removes or renames it. Pods of the previous version keep serving their own class name during the rollout. On a fresh installation there are no such pods, so while the foreign class exists no pod serves, and the fail-closed webhooks refuse every pool write until the hoster removes or renames it.
- A pool whose first classed render failed at the template guard is repaired with `workerclass-restore --repair`. It accepts only a classed HelmRelease whose live MachineDeployment carries the `renderer` marker and no class annotation. It renders the class settings without the class through the chart revision that rendered the live MachineDeployment, which the operator supplies: the previous revision if a new one was served before the guard failed. It writes them back only if that render names the bootstrap and infrastructure templates the live MachineDeployment carries. The write sets the migration hold, and the operator removes the hold to retry. If the failure came from another pool edit made after the migration patch, the operator reverts that edit first, or the repair refuses. Restore without `--repair` needs a successful classed render.
- A pool the controller cannot migrate stays on the legacy path, its tenant cannot change its OS fields, and the later release refuses to upgrade while it exists. The remedy is the hoster correcting the stored values in its HelmRelease, after which the controller migrates it.
- A generated class may exist without its finalizer yet, since the leader's controller adds it. The validating webhook refuses such a request with the stack's "not yet protected against deletion; retry in a few seconds" message, and a retry succeeds. New in this design, it answers the same retry message, not `WorkerClass "<name>" does not exist`, for a generated name whose class is not there yet. An unreferenced generated class that was deleted is recreated by the leader, so the retry message covers that window too.
- A platform class that is deleting while pools still use it stays Terminating behind its finalizer until the last reference leaves. A new pool on it is refused with a diagnostic for the operator, not a retry message: choose another `defaultWorkerClass`, or change `kubernetesWorkerClasses.platform.talos` to get a new class name; for Kairos, publish a new disk in the ConfigMap, which gives a new class, or use a hoster class. Existing pools do not change by themselves. An UPDATE that keeps the deleting class, or moves a pool from it to another class, is allowed; an explicit class change may roll the pool.
- Upgrade window: a pool that gets its class while the previous chart still renders it, created through the API after the new webhooks serve, renders with the previous chart, which ignores `workerClassName`. Its MachineDeployment has no `renderer` marker, so the guard of Design item 5 does not apply, and the pool rolls once when the class-aware chart first renders it, if the class differs from what the previous chart rendered. This is a known limit of the upgrade window.
- Kairos alias with no `version` while an installation default sets one, when a generated class exists for the chart's default minor: the mutator picks the chart's default minor before the merge, and the validator refuses the mismatch.

## Testing

- Render equivalence before and after migration for every legacy shape (factory with v1.13.6 and v1.13.12 stored defaults, builtin, Proxmox, `kernelModules` set and absent, NVIDIA `gpus`, sysctls, values copied by installation defaults): identical TalosConfigTemplate, machine template and MachineDeployment template digests. Controller: no patch without the required markers, name collision, unresolvable pool, interruption at each step. ComputePlane: template names unchanged at migration, a stored class survives a platform class change. Platform chart handling of the deprecated entry; upgrade and rollback e2e.

## Rollout

One release, as in Upgrade; the pull request structure is in the Overview.

## Open questions

1. Is ComputePlane's `lookup` of its own HelmRelease acceptable under the repository's lookup rules?
2. Should unreferenced generated classes be collected, and if so, immediately or after a retention period?
3. Refuse new references to `legacy-` classes, or let a tenant add pools on its own legacy class?
4. When does the deprecated `applicationDefaults.kubernetesnodeses.talos` input stop being converted and start failing the render?

## Alternatives considered

- No installation defaults on classed pools: removes the air-gap path for every new pool.
- Installation defaults selected by family or class: core reads WorkerClass, and values still override the class.
- A hoster-written default class only: built-in `talos` still renders chart defaults. WorkerClass as an installation-defaults target: a CRD, not an application. A baseline behind the built-in class read at render: rolls every pool on change.
- A pool value equal to the current default counts as unset: a changed default rolls pools.
- The webhook writes explicit empties for class-owned fields: keeps overrides, object-level precedence and render-time built-in defaults. Explicit nulls per key: the schema refuses null for several keys, and nulls collide with Helm's null-deletes-key behavior. Installation defaults record the keys they filled: reverses no-provenance and helps only pools created later.
- A Helm hook Job of the worker package for the migration: holds the package release not Ready while pools upgrade and fails on hook timeout.
- Keep tenant overrides: every collision comes from that second source of OS settings. Variation can return as schema-declared class parameters.
- A fully generic class that references bootstrap and infrastructure templates instead of typed settings, the way ClusterClass (in the prior art below) does. A new OS would then be an object, not a code change. Rejected for now, because the platform would lose what the typed class gives at small cost: admission-time validation of OS settings (formats, Talos and Kubernetes compatibility, family rules for `sshKeys` and `gpus`, substrate contracts), a content-hashed class identity computed from a known spec, and the automatic migration, which computes a typed class from a pool's effective settings and is guarded by the pool chart refusing a render that would rename templates. A generic family could be added to the class later without adding pool fields.
- Prior art: [StorageClass](https://kubernetes.io/docs/concepts/storage/persistent-volumes/), [LimitRange](https://kubernetes.io/docs/concepts/policy/limit-range/), [ClusterClass](https://cluster-api.sigs.k8s.io/tasks/experimental-features/cluster-class/write-clusterclass), [Gardener](https://gardener.cloud/docs/gardener/api-reference/core/).
