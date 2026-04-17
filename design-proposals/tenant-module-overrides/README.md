# Tenant module overrides

- **Title:** Per-tenant overrides for bundled tenant modules (etcd, seaweedfs, monitoring, ingress)
- **Author(s):** _TBD by submitter_
- **Date:** 2026-04-17
- **Status:** Draft

## Overview

Cozystack tenants can opt into four bundled modules (`etcd`, `monitoring`, `ingress`, `seaweedfs`) via boolean flags on the `Tenant` chart. Every `true` renders a `HelmRelease` whose values come only from the cluster-wide `cozystack-values` Secret — admins have no per-tenant override path short of disabling the flag and hand-rolling a `HelmRelease`, which drops tenant namespace scaffolding, `ApplicationDefinition` wiring, dashboard/sharding/cleanup labels, and quota bindings.

This proposal replaces each bool flag with a typed object carrying a `valuesOverride` field whose schema is embedded from the underlying chart at build time via a new `@embed` directive in `cozyvalues-gen`. A platform migration rewrites existing manifests in place. Piloted on `etcd`; `monitoring`, `ingress`, and `seaweedfs` follow the same primitive in a later release with no new design.

## Scope and related proposals

This is the first of three proposals concerned with how Cozystack admins customize a running platform. The other two are out of scope here and will be submitted as sibling proposals:

- **Admin customization guide and overlay tooling** — documenting and extending the existing platform-level override mechanics (`Package.spec.components.*.values`, `bundles.enabledPackages`, variants), authoring custom third-party packages, and testing flags such as konnectivity-over-hostNetwork.
- **Multi-etcd-per-tenant** — addressing the architectural fact that every Kubernetes cluster inside a single tenant shares one etcd via `dataStoreName` in `packages/apps/kubernetes/templates/cluster.yaml`.

The pilot module for *this* proposal is `etcd`. `monitoring`, `ingress`, and `seaweedfs` ride the same primitives once they land.

## Context

The `Tenant` chart (`packages/apps/tenant`) lets admins opt each tenant into four bundled modules via booleans in `values.yaml`:

```yaml
etcd: false
monitoring: false
ingress: false
seaweedfs: false
```

Each `true` renders a fixed `HelmRelease` with hard-coded name, hard-coded namespace, and values exclusively from the cluster-wide `cozystack-values` Secret. See `packages/apps/tenant/templates/etcd.yaml`, `seaweedfs.yaml`, `monitoring.yaml`, `ingress.yaml`.

### The problem

1. **No override path.** If the cluster default doesn't fit (wrong storage class, wrong replica count, default too big for the cluster), the admin has nowhere to put per-tenant overrides.
2. **The escape hatch breaks inheritance.** The documented workaround — set `etcd: false`, then apply a hand-written `HelmRelease` with nested `spec.values` — drops tenant namespace scaffolding, `ApplicationDefinition` wiring, labels consumed by dashboard/sharding/cleanup, and quota bindings.
3. **GitOps-hostile.** The workaround forces a "deploy the wrong thing first, then hand-edit it" flow. If the default config exceeds cluster resources, step one fails, and there's no path to fix.
4. **Dashboard friendship.** Admins editing a `Tenant` resource in the UI have no fields for any module knob — just checkboxes.

## Goals

1. Admins can configure any per-tenant `etcd` chart knob from the `Tenant` resource, with a typed, validated schema.
2. No existing `Tenant` manifest using `etcd: true` / `etcd: false` breaks.
3. The mechanism is a **reusable primitive** for the three follow-up modules and for other future "this wrapper chart embeds that subchart's schema" cases.
4. Keeps cozyvalues-gen as the single source of schema truth; no hand-duplicated schemas.
5. Per-tenant values deep-merge onto cluster-wide `cozystack-values` defaults. Tenant wins.

### Non-goals

- **Multi-etcd-per-tenant** (project C). Tenants still get at most one of each module; the architectural "one etcd shared by many k8s clusters" issue is orthogonal.
- **Custom tenant modules / sideloaded HelmReleases.** Admins who want to drop arbitrary HelmReleases into a tenant namespace keep doing so today. Project B will document that path and add overlay tooling.
- **State cleanup on mutation.** Changing `storageClass` or `size` after first apply will not relocate existing PVCs. This is StatefulSet semantics and out of scope. Documented as a caveat ("plan capacity before creation; recreate to change storage").
- **Seaweed's vendored Postgres filer store.** Exposing the filer-store sub-schema for configuration is part of the seaweedfs follow-up, not this pilot.
- **UI/dashboard changes beyond schema regeneration.** New fields show up in the existing generic form automatically once `values.schema.json` regenerates.

## Design

### 1. Tenant values.yaml: typed module schema

Before (`packages/apps/tenant/values.yaml`):

```yaml
## @param {bool} etcd - Deploy own Etcd cluster.
etcd: false
```

After:

```yaml
## @embed packages/extra/etcd as EtcdOverrides
##
## @typedef {struct} EtcdModule - Etcd tenant module.
## @field {bool} enabled=false - Deploy own Etcd cluster for this tenant.
## @field {EtcdOverrides} [valuesOverride] - Override values for the etcd chart.
##
## @param {EtcdModule} etcd - Etcd tenant module.
etcd:
  enabled: false
  valuesOverride: {}
```

Key points:

- `etcd` becomes an object. The `enabled` boolean replaces the old top-level bool.
- `valuesOverride` is the typed override bag. Its schema is sourced from the embed target at build time; admins see every field the underlying etcd chart exposes. The field name matches the convention already established by the kubernetes app's addons (`packages/apps/kubernetes/values.yaml`, e.g. `addons.certManager.valuesOverride`).
- `valuesOverride` defaults to `{}` so most users see an empty object and fall through to cluster-wide defaults.
- Sibling follow-up modules (`monitoring`, `ingress`, `seaweedfs`) get identical treatment, no further design needed.

### 2. `@embed` directive in `cozyvalues-gen`

New annotation in [cozyvalues-gen](https://github.com/cozystack/cozyvalues-gen):

```
@embed <package-path> as <TypedefName>
```

Semantics:

- **Resolves the target** by reading its `values.yaml` annotations.
- **Walks the annotation tree transitively**, pulling in every `@typedef` and `@enum` the target's `@param`s transitively reference.
- **Emits a synthetic typedef** (`<TypedefName>` — here `EtcdOverrides`) in the host schema whose properties are the target's top-level `@param`s.
- **Renames conflicts** by namespacing imported typedefs: `EtcdOverrides.Resources` becomes a distinct type from any host-local `Resources` typedef. Implementation: generator prefixes imported type names with the `as` alias.
- **Preserves defaults** from the target package. The tenant chart doesn't re-declare them.
- **Fails loudly** on:
  - Missing target package.
  - Circular embeds (`A @embed B @embed A`).
  - Attempts to embed a non-annotated package.
- **No field renaming or field subsetting** in this pilot. The entire target surface is exposed. Subset/rename semantics can be added later if a real use case appears.

Generator output side effects:

- Tenant chart's `values.schema.json` gains the full etcd sub-schema under `properties.etcd.properties.values`.
- Tenant chart's generated README lists the embedded schema inline, with a reference note pointing at the source package.
- Generator adds the target package's `values.yaml` path to a sidecar manifest so that `make generate` knows the tenant chart must be regenerated whenever `packages/extra/etcd/values.yaml` changes. (Concretely: a one-line `# cozyvalues-gen:embed packages/extra/etcd` header in the generated artifacts so a pre-commit check flags stale generation.)

Embed is a **build-time** primitive. There is no runtime resolution; the generated `values.schema.json` and `README.md` are fully materialized. This matches existing generator behavior.

### 3. Helm template change: render an overrides Secret, append to `valuesFrom`

Each module template normalizes bool-form to object-form and, when `enabled`, renders two objects: the HelmRelease (unchanged except for an extra `valuesFrom` entry) and a Secret carrying the overrides.

`packages/apps/tenant/templates/etcd.yaml` (schematic):

```gotemplate
{{- $etcd := .Values.etcd }}
{{- if kindIs "bool" $etcd }}
  {{- /* Bool-form compatibility shim. Normalized to object-form. */ -}}
  {{- $etcd = dict "enabled" $etcd "valuesOverride" dict }}
{{- end }}
{{- if $etcd.enabled }}
---
apiVersion: helm.toolkit.fluxcd.io/v2
kind: HelmRelease
metadata:
  name: etcd
  namespace: {{ include "tenant.name" . }}
  labels:
    # (unchanged)
spec:
  chartRef:
    kind: ExternalArtifact
    name: cozystack-etcd-application-default-etcd
    namespace: cozy-system
  interval: 5m
  timeout: 30m
  install:
    remediation:
      retries: -1
  upgrade:
    force: true
    remediation:
      retries: -1
  valuesFrom:
  - kind: Secret
    name: cozystack-values
  - kind: Secret
    name: tenant-etcd-values
    optional: true
---
apiVersion: v1
kind: Secret
metadata:
  name: tenant-etcd-values
  namespace: {{ include "tenant.name" . }}
  labels:
    app.kubernetes.io/instance: {{ .Release.Name }}
    app.kubernetes.io/managed-by: {{ .Release.Service }}
type: Opaque
stringData:
  values.yaml: |
{{ toYaml (default dict $etcd.valuesOverride) | indent 4 }}
{{- end }}
```

Precedence: Flux merges `valuesFrom` in order. `cozystack-values` first sets cluster-wide defaults; `tenant-etcd-values` merges on top → **tenant wins**. `optional: true` so that a tenant with `etcd.valuesOverride: {}` still works (the Secret exists, but an empty-values-Secret is a safe no-op).

The bool-form compatibility shim stays in the template for at least two minor releases. Deprecation notice lands in the first release; removal in the third.

### 4. Platform migration: bool → object

The platform already runs migrations on `Tenant` values through `packages/core/platform/templates/migration-hook.yaml`.

Add a new migration (next sequential version at implementation time) that:

1. Lists all `HelmRelease` resources for the `cozystack-tenant` chart.
2. For each, if `spec.values.etcd` is a bool: rewrites it to `{enabled: <bool>, valuesOverride: {}}`.
3. Same for `monitoring`, `ingress`, `seaweedfs` — so the three follow-up modules don't need their own migration.
4. Is idempotent (checks the shape before rewriting).
5. Ships in the same release as the Helm template change.

`packages/core/platform/values.yaml`:

```yaml
migrations:
  enabled: true           # flip on if previously disabled
  image: ghcr.io/cozystack/cozystack/platform-migrations:vX.Y.Z@sha256:...
  targetVersion: <next>   # bumped to the new migration's version
```

Migration image is a small Go binary that uses the platform's existing migration framework (see the prior migrations under `packages/core/platform/images/migrations/migrations/` for the idiom).

## User-facing changes

- **Tenant resource schema.** `etcd` (and later `monitoring` / `ingress` / `seaweedfs`) change from bool to object. New manifests use `etcd: {enabled: true, valuesOverride: {...}}`; the old bool form keeps working via the template shim for two minor releases.
- **Dashboard form.** Regenerates from the new `values.schema.json`. The generic form renderer already handles nested objects; every field the underlying chart exposes shows up as an expandable `etcd.valuesOverride` section per module.
- **`ApplicationDefinition` for etcd.** Already exists (`packages/system/etcd-rd/cozyrds/etcd.yaml`) for the standalone-etcd case and is unchanged.
- **`cozystack-api`.** No API change — `Tenant` still takes a single values blob; the blob's schema is richer.
- **Docs.** One new page `docs/v1.2/operations/configuration/tenant-modules.md`, linked from `components.md`, explaining the object-form schema with a before/after for `etcd` and the caveats (no in-place storage-class swap, no multi-instance, no custom-module sideloading yet). The existing `components.md` page gets a one-line link plus a short note that per-tenant module overrides are documented separately. The broader admin-customization guide (site overrides / test flags / custom packages) is sibling Project B — not produced here.

## Upgrade and rollback compatibility

- **Existing `Tenant` manifests.** Unchanged at rest. The new platform migration (Design §4) rewrites `etcd: <bool>` → `{enabled: <bool>, valuesOverride: {}}` in place; idempotent, runs once per cluster. The same migration covers all four modules, so the follow-up releases need no new migration of their own.
- **In-template compatibility shim.** The Helm template normalizes bool-form to object-form in Go template land, so a `Tenant` that somehow bypassed the migration (third-party tooling, hand-written manifest) still renders correctly. The shim stays for at least two minor releases; removal in the third (see Rollout).
- **Rollback.** Reverting the chart removes the new `valuesFrom: tenant-<module>-values` entry on the HelmRelease. The overrides Secret may linger in the tenant namespace (harmless; the old chart's selector doesn't GC it). Rolling back past the migration is unsupported — the platform migration framework does not run reverse migrations, and the rewritten object shape is not recognized by the pre-migration chart. Admins who want to keep the option to roll back should snapshot `Tenant` CRs before upgrade.
- **Escape-hatch users.** Admins who have already hand-written a HelmRelease workaround keep working: the new chart only creates its HelmRelease when `enabled`. The new docs page invites them to switch to the typed form.

## Security

No new trust boundary. The override path is admin-only (`Tenant` resources are admin-owned) and merges onto cluster-wide defaults rather than replacing them. Tenants themselves cannot set `valuesOverride`; they do not edit their `Tenant` CR. No new secrets are transmitted — overrides land in a Secret inside the tenant namespace that the tenant can already read.

One caveat worth flagging: the full target chart surface is exposed to admins (see Design §2). If a future embedded chart has fields that should not be admin-tunable (e.g. pointers at shared control-plane infrastructure), a field-blocklist on `@embed` is the intended cut — tracked in Open questions.

## Failure and edge cases

- **Admin disables etcd but keeps old `valuesOverride` content**: harmless. The Secret is never rendered because we gate on `enabled`. On re-enable, overrides come back.
- **Admin sets invalid values** (e.g., bad `storageClass`): the underlying chart rejects the HelmRelease. Flux surfaces the error on the HelmRelease status, which the dashboard already shows per-tenant.
- **Cluster-wide `cozystack-values` sets `etcd.size: 20Gi`, tenant sets `etcd.valuesOverride.size: 10Gi`**: tenant wins (10Gi). Documented explicitly in the new page.
- **Migration runs on a v1.2 cluster that never touched `etcd`**: the field is already `false`; migration rewrites to `{enabled: false, valuesOverride: {}}` — safe.
- **Admin has already hand-written a HelmRelease workaround**: unchanged. The workaround continues working because the Helm template only creates its HelmRelease when `enabled`. Doc update invites them to switch to the typed form.

## Testing

- **Helm unit tests** (`packages/apps/tenant/tests/`): cases for bool-true, bool-false, object-enabled, object-disabled, object-enabled-with-overrides, object-enabled-empty-overrides.
- **cozyvalues-gen unit tests**: `@embed` resolution, nested typedef rename, circular-embed detection, missing-target error, stale-generation detection.
- **E2E**: extend `hack/e2e-apps/` with a BATS case that creates a tenant with `etcd.valuesOverride: {replicas: 5, size: 10Gi, storageClass: replicated}`, waits for the StatefulSet, and asserts it matches.
- **Migration test**: fixture `Tenant` HelmRelease with old bool form, run migration, assert rewritten shape.

## Rollout

1. **Release N** (this spec's target): ship `@embed` in cozyvalues-gen, tenant chart change for **etcd only**, the new platform migration, docs page. Bool form still supported via the template shim.
2. **Release N+1**: monitoring, ingress, seaweedfs migrated to the same pattern. No new primitives. Deprecation warning added in release notes for bool form.
3. **Release N+2**: remove the bool-form shim from the template.

The new migration already handles all four modules, so the N+1 releases are pure chart edits.

## Open questions

1. Migration image versioning: the existing `ghcr.io/cozystack/cozystack/platform-migrations` image is pinned with a digest. The new migration is one more file in the same image; cut point for rebuild is the target release.
2. Whether to add a field-blocklist form to `@embed` (e.g. `@embed <pkg> as Foo except: [internalDb]`) preemptively, or defer until a concrete need arises in a later embed target (the most likely candidate is the SeaweedFS follow-up, because its upstream chart vendors an internal Postgres filer store whose knobs we may not want to expose to tenants).

## Alternatives considered

### Data model

- **Boolean flag plus sibling `etcdValuesOverride: {}`.** Rejected — proliferates top-level fields per module, and co-locating `enabled` with its overrides in one object is both cleaner and matches the existing addon pattern in `packages/apps/kubernetes/values.yaml`.
- **Polymorphic field accepting either bool or object.** Rejected — harder to validate via JSON Schema, confusing for the dashboard form renderer, and interleaves deprecation with type-casting.
- **Object form with a migration from the bool form** (chosen). Zero breakage for existing `Tenant` manifests thanks to the existing platform migration hook; clean long-term shape.

### Override surface

- **Free-form `valuesOverride: {}` passthrough** (addons pattern in kubernetes app). Rejected — user-facing Cozystack apps are expected to carry validated schemas, and free-form surfaces undermine the dashboard's form-generation story.
- **Curated typed subset hand-written per module.** Rejected — large maintenance tax; drifts from the underlying chart whenever a new knob is added to `packages/extra/*`.
- **Typed surface auto-generated from the embed target** (chosen). Reuses `cozyvalues-gen` as the single source of schema truth; every knob the underlying chart exposes is surfaced automatically, validated, and documented in the Tenant README.

### Runtime merge

- **Inline `spec.values:` on the HelmRelease.** Rejected — precedence semantics (inline vs `valuesFrom`) are confusing, and the rendered override is harder to inspect.
- **New `TenantModuleValues` CRD + controller.** Rejected as overkill for this iteration; adds a moving part for something Flux already handles.
- **Render a Secret per-module and append to `valuesFrom`** (chosen). Idiomatic Flux; precedence is explicit and positional; admins can `kubectl get secret tenant-<module>-values -o yaml` to see exactly what is being merged.

### Schema sync mechanism

- **Hand-copy `values.yaml` fields into the Tenant chart with a CI drift detector.** Rejected — maintenance cost grows with every schema change in every embedded chart; particularly bad for SeaweedFS's large surface.
- **`make generate`-driven copy script between packages.** Rejected as a strictly worse variant of the generator change.
- **New `@embed` directive in `cozyvalues-gen`** (chosen). One primitive reused by every future wrapper-of-subchart pattern; schema truth stays in one place.
