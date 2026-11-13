# RustFS as an optional S3 object storage application

- **Title:** `RustFS as an optional S3 object storage application`
- **Author(s):** `@themoriarti`

## Overview

Add RustFS (Apache-2.0, a distributed S3-compatible object store written in Rust) to Cozystack as a second object storage backend next to SeaweedFS. The first step is deliberately small: an opt-in tenant module, shaped like the existing `seaweedfs` module, that deploys a distributed RustFS cluster from the upstream Helm chart and exposes its S3 endpoint. It does not replace SeaweedFS and does not change the `Bucket` application. Whether and how RustFS should later back `Bucket` is the main open question (see below), because RustFS has no COSI driver today.

This proposal answers the request in [cozystack/cozystack#2146](https://github.com/cozystack/cozystack/issues/2146) and the maintainer's ask there to start from a design proposal.

## Scope and related proposals

- A working prototype exists on branch `feat/rustfs-system` of the `themoriarti/cozystack` fork (one commit on top of `main`). It is the reference for the shapes below, not a PR yet; it will be opened once this proposal is accepted.
- **Deferred:** COSI / `Bucket` integration, a `rustfs` tenant flag, per-tenant console access, multi-pool expansion, KMS, TLS between nodes, a mirrored/signed image.

## Context

SeaweedFS is today the only object storage in the platform. It ships as three packages:

- `packages/system/seaweedfs`: the vendored upstream chart and platform-level patches;
- `packages/extra/seaweedfs`: the tenant module (a `HelmRelease` of the system chart, ingress, `WorkloadMonitor`s, a dashboard `Role`, COSI `BucketClass`);
- `packages/system/seaweedfs-rd`: the `ApplicationDefinition`;

plus `cozystack.seaweedfs-application` as a `PackageSource` in the system bundle. A tenant gets buckets through the `Bucket` app, which creates a COSI `BucketClaim` and `BucketAccess` against the tenant's SeaweedFS class (`namespace.cozystack.io/seaweedfs`).

SeaweedFS is a good fit for many small objects, but it is several moving parts (master, volume, filer, a PostgreSQL filer store, S3 gateway, COSI driver) and the platform carries a growing patch set on top of its chart. RustFS is one StatefulSet of identical nodes: each node serves S3 and stores erasure-coded data on its own volumes, with no separate metadata service or database.

### The problem

- Users have asked for RustFS as an alternative ([#2146](https://github.com/cozystack/cozystack/issues/2146)).
- Operators who want a simple, single-component S3 store, with MinIO-like semantics and erasure coding instead of replication, cannot get one from the catalog.
- Operators who run object storage on a small number of nodes pay for the full SeaweedFS topology.

## Goals

- A tenant owner can create a `RustFS` module and get a distributed, erasure-coded S3 endpoint at `s3.<tenant host>` with TLS from the platform issuer, and a generated access/secret key pair in a Secret in the tenant namespace.
- The module is opt-in: it is not installed unless the operator enables `cozystack.rustfs-application`. Clusters that do not enable it see no change.
- The module follows the conventions of `seaweedfs`: system chart + extra chart + RD + `PackageSource`, `WorkloadMonitor` and dashboard `Role`, resources from `resourcesPreset`.
- The node count cannot be changed after first deploy by a values edit (it fixes the erasure-coded drive layout); the chart rejects it instead of corrupting the layout.
- Credentials are generated once and survive upgrades.

### Non-goals

- Replacing or deprecating SeaweedFS.
- Making `Bucket` work on RustFS in this proposal (see Open questions).
- Exposing the RustFS web console outside the cluster.
- Migrating data between SeaweedFS and RustFS.

## Design

### 1. Packages

| Package | Content |
| --- | --- |
| `packages/system/rustfs` | Vendored upstream chart (`rustfs/rustfs`, 1.0.5) under `charts/`; `make update` re-pulls it. No patches in the prototype. |
| `packages/extra/rustfs` | Tenant module: generated credentials Secret, `HelmRelease` of the system chart, `WorkloadMonitor`, dashboard `Role`/`RoleBinding`. |
| `packages/system/rustfs-rd` | `ApplicationDefinition` (`kind: RustFS`, a tenant module in the Administration category). |
| `packages/core/platform/sources/rustfs-application.yaml` | `PackageSource` `cozystack.rustfs-application`, listed in the system bundle with `package.optional.default`. |

### 2. Module values

```yaml
host: ""            # defaults to s3.<tenant host>
replicas: 4         # >= 2, fixed after first deploy
size: 10Gi          # capacity of each node (one drive per node)
storageClass: ""    # empty = cluster default
resources: {}
resourcesPreset: small
```

The module maps these onto the upstream chart: `replicaCount`, `drivesPerNode: 1` (so `size` is per node and the chart's legacy 4-drives-for-4-nodes default does not silently multiply capacity), `storageclass.*`, `resources`. Erasure-coding parity is left to the server default; exposing it is deferred because valid values depend on the drive count.

### 3. Endpoint and credentials

- Ingress: only the S3 endpoint (port 9000) on `s3.<host>`, class and `cert-manager.io/cluster-issuer` taken from the tenant, body size unlimited and buffering off for large uploads. The console (9001) is not routed.
- Credentials: a Secret `<name>-credentials` with `RUSTFS_ACCESS_KEY`/`RUSTFS_SECRET_KEY`, generated with `randAlphaNum` and kept on later renders by a `lookup`, the same approach as the SeaweedFS admin credentials. The upstream chart refuses default credentials and is pointed at this Secret via `secret.existingSecret`.
- The RD lists the credentials Secret, the `-svc` Service and the Ingress so the dashboard shows them.

### 4. Opt-in

`bundles/system.yaml` gets one line using the existing optional helper, so the package exists only when the operator enables it (or until the community decides it should be a default).

## User-facing changes

A new `RustFS` entry in the dashboard (Administration) and `apps.cozystack.io/v1alpha1` kind `RustFS`. Nothing changes for existing SeaweedFS or `Bucket` users.

## Upgrade and rollback compatibility

Purely additive and opt-in. Removing the module deletes its `HelmRelease` and, per StatefulSet semantics, leaves PVCs in place. Changing `replicas` after deploy is rejected at render time. Upstream chart upgrades go through `make update`; the StatefulSet `volumeClaimTemplates` are immutable, so changes to `drivesPerNode` or `size` of an existing install need a manual recreate. This is documented upstream and must be in the module docs.

## Security

- New tenant-reachable surface: a public S3 endpoint per module, authenticated by the generated key pair.
- Secrets: the credentials Secret lives in the tenant namespace; it is readable by whoever can read secrets there. The module does not create per-user keys.
- The image `rustfs/rustfs` comes from Docker Hub, unsigned by Cozystack in the prototype. Before this leaves the opt-in stage it should be mirrored to the Cozystack registry and pinned by digest, as is done for SeaweedFS.
- The upstream pod already runs non-root with a read-only root filesystem and dropped capabilities, so it should fit the tenant namespace's Pod Security profile; this must be verified in e2e.

## Failure and edge cases

- `replicas < 2`: render fails with a clear message.
- `replicas` changed after deploy: render fails, naming the current value.
- No tenant host and no `host` value: render fails through `cozy-lib.ns-host-required`.
- Re-render with an existing credentials Secret: keys are unchanged.
- Node loss within parity: S3 stays available; more nodes lost than parity: reads/writes fail until nodes return. Parity is the server default for the drive count.

## Testing

- Unit: `helm template` of both charts for default values and for each failure case above (to be added as helm-unittest tests in the PR).
- E2E: a `hack/e2e-apps/rustfs.bats` that creates the module in a test tenant, waits for the `HelmRelease`, then does a `mc` put/get through a port-forward with the generated credentials, mirroring `bucket.bats`.
- Manual: node restart and single-node loss on a 4-node install.

## Rollout

1. Opt-in module (this proposal's prototype), documented as experimental.
2. Image mirror and digest pin, e2e in CI, docs page in `cozystack/website`.
3. Decision on the `Bucket` integration (below); promotion out of "experimental" only after that.

## Open questions

1. **`Bucket` integration.** `Bucket` is built on COSI. We found no RustFS COSI driver. Options: (a) write a COSI driver (RustFS exposes an S3-compatible API and IAM-style admin API; its fitness for a driver needs to be checked); (b) a small Cozystack-side controller that creates the bucket and a scoped key directly; (c) leave RustFS as a raw S3 endpoint only. Which would maintainers accept?
2. **Default or optional?** Should this stay opt-in indefinitely, or become a default package once mature?
3. **Maturity gate.** What is the bar for shipping a third-party store the project must patch and support: a release age, a security audit, a CNCF-sandbox-or-similar status?
4. **Image policy.** Mirror upstream's image, or build from source like SeaweedFS?
5. **Per-tenant vs shared.** SeaweedFS is a tenant module that sub-tenants inherit through `namespace.cozystack.io/seaweedfs`. Should RustFS get an equivalent namespace label, or stay a plain per-tenant app?
6. **Capacity expansion.** Upstream supports append-only pools. Is that worth exposing in v1, given nodes cannot be rescaled in place?

## Alternatives considered

- **Do nothing, use SeaweedFS only.** Simplest for the project, but ignores the request and keeps the multi-component footprint for small installs.
- **Run RustFS outside Cozystack and consume it as an external S3.** Works today, but is not managed, monitored or tenant-isolated by the platform.
- **MinIO.** Same architectural shape, but the community edition has moved to maintenance/source-only distribution, so it is a poor dependency for a platform. (Maintainers should correct this if they have better information.)
- **Rook-Ceph RGW.** Mature S3, but requires a Ceph cluster, far heavier than the use case here.
- **Replace SeaweedFS with RustFS.** Out of scope: different workload profile, no COSI driver, and a breaking change for every existing `Bucket`.
