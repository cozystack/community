# Cozystack API v1

- **Title:** `Cozystack API v1`
- **Author(s):** `@kvaps`
- **Date:** `2026-09-07`
- **Status:** Draft

## Overview

Everything a tenant writes today is `apps.cozystack.io/v1alpha1`. It has been that way since the beginning, and people run production on it. This proposal promotes it to `v1` and says what `v1` guarantees.

Two things have to leave first. Backups moved to `backups.cozystack.io` a while ago, but the old `spec.backup` fields are still there. External addresses are moving to `EndpointAttachment` (#45). Carrying both into `v1` would freeze the wrong ones forever.

## What a tenant gets

Today, one resource does everything:

```yaml
apiVersion: apps.cozystack.io/v1alpha1
kind: Postgres
metadata: {name: mydb}
spec:
  replicas: 2
  external: true                # publishes on some address, chosen for you
  backup:
    enabled: true
    schedule: "0 2 * * *"
    s3AccessKey: AKIA...        # your S3 keys, in the resource
```

After `v1`, the database is a database, and the other two are their own objects:

```yaml
apiVersion: apps.cozystack.io/v1
kind: Postgres
metadata: {name: mydb}
spec:
  replicas: 2
---
apiVersion: cozystack.io/v1alpha1
kind: EndpointAttachment        # an address you can keep, move, and pick the pool for
spec:
  applicationRef: {kind: Postgres, name: mydb}
  endpoint: {serviceName: postgres-mydb-rw}
  loadBalancer: {className: public}
---
apiVersion: backups.cozystack.io/v1alpha1
kind: Plan                      # schedule and retention, no credentials
spec:
  schedule: "0 2 * * *"
```

Four practical differences:

- **A typo is an error, not silence.** Write `spec.replcas: 3` today and the API accepts it, stores it, and does nothing. In `v1` it fails and tells you which field.
- **Fields that do nothing are gone.** `spec.nodeGroups` on a Kubernetes cluster has been dead since Phase 2 and still looks alive.
- **One place per thing.** Backups in `backups.cozystack.io`, addresses in `EndpointAttachment`. Not two ways, one of them wrong.
- **A promise.** Inside `v1` we do not remove or retype a field. If we have to, it becomes `v2` and the old version keeps working for at least two minor releases and six months.

Nobody has to do anything at upgrade. Existing manifests keep working against `v1alpha1`; the platform converts in the background.

## Why we cannot just delete the fields today

Removing a field from a chart's schema does nothing. The API accepts it anyway.

The published `spec` schema is marked open for every chart (`pkg/cmd/server/openapi.go:169-171`), and nothing validates `spec` server-side — the structural schema is built and used only for defaults (`rest.go:107-127`, `rest_defaulting.go:30-42`). So a deleted field is still accepted, still stored, and silently inert.

That is why `rest.go:1932` carries a hardcoded list of dead Kubernetes fields and warns about them by hand. Every removal so far has had to add another list.

`v1` closes the object: unknown fields are rejected on write. That single change is what makes removal mean anything, and it is cheap — the schema is already there.

One caveat: an upgraded cluster may hold stored values with fields `v1` does not know. Those are tolerated in storage and reported on read, never silently dropped. Only writes are strict.

## What moves where

| Leaves `v1` | Where it goes | Kinds affected |
|---|---|---|
| `spec.backup.*`, `spec.bootstrap.*` | `BackupClass` + `Plan` + `RestoreJob` in `backups.cozystack.io` | Postgres, MariaDB, MongoDB, ClickHouse, FoundationDB |
| `spec.external`, `externalMethod`, `externalPorts`, `externalAllowICMP`, `externalIPs` | `EndpointAttachment` (#45) over `IPAddressClaim` (#35) | 15 kinds |
| `nodeGroups`, `nodeHealthCheck`, `maxNodeProvisionTime` | `KubernetesNodes` (already shipped) | Kubernetes |
| `subnets` | `networks` (already there) | VMInstance |
| flat `resourcesPreset` names (`nano`…`2xlarge`) | `<class>.<size>` form (already there) | all |

Two things make this harder than it looks, and both must be fixed before the fields can go:

**The restore driver writes those backup fields.** `spec.backup` and `spec.bootstrap` are not only tenant input — the CNPG driver patches them on the Postgres resource to perform a restore (`cnpgstrategy_controller.go:1078-1140`). It needs another channel first. Cheapest option: the keys stay in `values.yaml` as platform-only fields, hidden from the tenant, driver unchanged.

**`external` is four switches in one.** Besides the LoadBalancer it also decides certificate SANs (`certmanager.yaml` in five charts), turns TLS on by default (kafka, qdrant, nats), shows things in the dashboard (kafka), and sets `externalTrafficPolicy` (vpn). Dropping it blindly would change TLS and certificates on upgrade. The conversion template resolves each of those into an explicit value first:

```gotemplate
tls:
  enabled: {{ if hasKey .tls "enabled" }}{{ .tls.enabled }}{{ else }}{{ .external | default false }}{{ end }}
```

## How a field actually leaves

Four steps, in this order:

1. The replacement is released and documented.
2. The field is marked deprecated and the API warns when you use it. This should come from a schema annotation generated by `cozyvalues-gen`, not from another Go list.
3. A platform migration (#58) creates the replacement objects — the same address, the same schedule — and verifies they work.
4. Only then does `v1` drop the field.

Steps 3 and 4 are different mechanisms on purpose. Creating an `EndpointAttachment` needs cluster access; a conversion template is a pure function over data and cannot do it. Keeping them apart gives the safety rule:

> The switch to `v1` storage refuses to run while any release still has a field to remove and no replacement object.

The chart value outlives the API field. It disappears from the contract while storage still carries it, and that gap is what makes the switch invisible to a running cluster.

`ApplicationDefinition` multi-version conversion (#6) is a hard prerequisite for all of this. It also needs one addition: its round-trip CI check cannot pass across a removal, so each version should declare `dropped: [<key>]` and the check should skip exactly those.

## Kind by kind, not all at once

`apps.cozystack.io/v1` serves the kinds that are ready and grows.

The reason is concrete. Kafka's `external` cannot be removed until the allocated address is written back into the broker's advertised listeners, and #45 says that work has no owner. A single cutover would block Postgres on that. Per-kind also matches how #6 and #46 already work: conversion is written per application, and #46 makes the package the version unit.

| Kind | Blocked by |
|---|---|
| Bucket, Harbor, VMDisk, VPC, KubernetesNodes | nothing — first cohort |
| Kubernetes | nothing beyond the `v1` machinery |
| Postgres, MariaDB, ClickHouse, FoundationDB | re-homing the restore driver's channel |
| the 11 passthrough kinds with `external` | #45 phase 1 + #35 adoption path |
| VMInstance | #45 phase 2 (`WholeIP` / `PortList`) |
| Tenant, Etcd, Monitoring, Ingress, SeaweedFS, ComputePlane, BootBox, ExternalDNS | #39 and #25, which are changing these specs now |
| Kafka, NATS, MongoDB | discovery write-back, currently unowned |

Out of scope: `core.cozystack.io` (TenantSecret is being redesigned, Tap is weeks old), `sdn.cozystack.io` (#35 disputes which group SecurityGroup belongs in), `cozystack.io` (four proposals are adding fields to `ApplicationDefinition`; #46 wants one consolidating pass first).

## Order of work

1. #6 lands, with `dropped:` included.
2. The `v1` machinery lands unused: strict validation, service fields, schema-driven deprecation warnings.
3. First cohort: Bucket, Harbor, VMDisk, VPC, KubernetesNodes. Nothing to migrate — proof the machinery is harmless.
4. Kubernetes, taking the dead Phase 2 fields with it.
5. Backups removal, after the restore driver moves.
6. `external` removal, after #45 phase 1.
7. Tenant and the module kinds, after #39 and #25.
8. Per kind, the window ends: `v1alpha1` withdrawn, chart values deleted.

## Compatibility

- Upgrade changes nothing observable. The migration creates objects; the storage switch only runs where it succeeded.
- Rollback inside the window works: `v1alpha1` is still served and conversion runs both ways.
- One thing a rollback does not undo: objects the migration created. So the migration leaves the old LoadBalancer Service in place and lets the attachment be additive; the old Service is removed only at the very end.

## Security

- Net reduction: closing the object stops arbitrary tenant JSON being stored verbatim, and S3 keys leave tenant-writable fields on five kinds.
- No new kind, controller, or RBAC.
- Honest caveat: `v1alpha1` stays open for the whole window, so this is a correctness boundary, not a security boundary, until the old version is withdrawn.

## Testing

- Unit: strict `v1` vs open `v1alpha1`; tolerate-in-storage but reject-on-write; deprecation warnings from the schema, with both hardcoded Go lists deleted.
- Unit: `to`/`from` on golden samples per kind, including the kafka TLS resolution above.
- e2e on Postgres: create with `backup` and `external` set, migrate, assert the `Plan` reconciles and the `EndpointAttachment` reports **the same address**, then read the resource as both versions with an external client connected throughout.
- e2e negative: attachment path disabled, assert the switch refuses and names the release.

## Open questions

1. Undeclared stored fields: warn on every read, or set a condition on the resource once? A condition is easier to find, but application status is a projection and every extra lookup slows reads.
2. After the switch, a client still writing `v1alpha1` re-creates the old inherited TLS default on every write. Freeze `v1alpha1` defaulting, or let it follow `v1`?
3. Who signs off a promotion? `cmd/api-gate` routes API changes to one owner; this probably needs both.

## Alternatives considered

**Promote as-is, remove nothing.** Freezes `spec.backup` and `spec.external` permanently. Cheaper now, worse forever.

**Go to `v1beta1` first.** Needs the same machinery and the same removals. Buys a name, not a mechanism. Reasonable fallback if #6 slips badly.

**Delete the fields at `v1alpha1`.** Already tried with `nodeGroups`. It is not deletion: the field stays accepted, stored and dead, and we add another hardcoded list.

**One cutover for the whole group.** Blocks everything on Kafka's unowned work item.

**Keep `external` as a shortcut that creates an `EndpointAttachment`.** Puts the attachment's lifecycle back inside a Helm upgrade and leaves the field in `v1` — the two things this is trying to fix.
