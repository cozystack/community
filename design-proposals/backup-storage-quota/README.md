# Quota and accounting for backup storage

- **Title:** `Quota and accounting for backup storage`
- **Author(s):** `@lllamnyp`
- **Date:** `2026-08-04`
- **Status:** Draft

## Overview

Cozystack can back up managed applications, but it cannot limit how much backup
storage a tenant consumes, nor tell anyone how much has been consumed. A tenant
with a cron `Plan` and a large database fills the operator's object storage
without ever meeting a limit or seeing a number.

This proposal adds a size- and count-aware backup quota. It has three parts,
and the interesting one is not the part people expect: enforcement is
straightforward, while *accounting* is the hard problem, because the set of
`Backup` objects in a namespace is not a faithful record of the bytes a tenant
occupies.

## Context

The backup API (`api/backups/v1alpha1`) is driven by four objects:

- `Plan` — a cron schedule. `internal/backupcontroller/plan_controller.go`
  creates one `BackupJob` per tick.
- `BackupJob` — a request for one run, resolved against a `BackupClass` to a
  driver-specific strategy.
- `Backup` — the record of a completed artifact. Created *by the strategy
  controller*, not by the user: see `etcdstrategy_controller.go`,
  `mariadbstrategy_controller.go`, `foundationdbstrategy_controller.go`. It
  carries `status.artifact.sizeBytes` (optional — "if known").
- `RestoreJob` — the reverse path.

Tenant quotas today are helm-driven: `packages/apps/tenant/templates/quota.yaml`
renders `.Values.resourceQuotas` through `cozy-lib.resources.flatten` into a
single `corev1.ResourceQuota`. Every dimension it can express is one
kube-apiserver already knows how to count.

### The problem

**Tenant:** "How much backup storage am I using? Am I close to a limit?" There
is no answer. The first feedback is an operator email.

**Operator:** "Tenant X is consuming 40 TB of object storage in backups." There
is no mechanism to cap it, and no per-namespace figure short of querying the
storage backend and reverse-engineering key prefixes.

## Goals

- Cap total backup storage per namespace, and refuse new backup runs once the
  cap is reached.
- Publish current backup consumption where a tenant can read it.
- Work for every driver, including those whose artifacts Cozystack does not
  own or delete.
- Never break an already-admitted run, and never block a restore.

### Non-goals

- **Capping an individual backup's size at admission.** The size of a backup is
  unknown until the artifact exists; `status.artifact.sizeBytes` is written
  afterwards. Any gate is therefore a stop-signal, not a ceiling — see
  "Overshoot is unbounded" below.
- **Purging artifacts to make room.** Retention belongs to each driver's own
  policy (barman `RetentionPolicy`, mariadb-operator `MaxRetention`, …).
- **Pricing or billing.** This proposal produces a number; charging for it is
  out of scope.

## Design

### 1. Why a quota Evaluator is not an option

The natural design is a `quota.Evaluator` for `backups.cozystack.io`, matching
how native resources are counted. It cannot be built. Evaluators are compiled
into kube-apiserver's ResourceQuota admission plugin and into
kube-controller-manager; there is no extension point that lets an out-of-tree
component contribute a *scalar* dimension derived from a custom resource's
status. A CRD gets exactly one thing for free: the generic object-count
dimension, `count/backups.backups.cozystack.io`.

And that freebie is a trap. Enforcing it means kube-apiserver rejects `Backup`
CREATE — which is issued by the strategy controller when a run finishes, not by
the tenant. A tenant at their limit would see backup *jobs* start, run to
completion, and then fail to record their result, leaving orphaned artifacts in
object storage with no CR referencing them. Exactly the wrong failure.

This yields the central rule of the design:

> **Gate the request, account the artifact.** Enforcement attaches to
> `BackupJob` CREATE, which the tenant (or their `Plan`) initiates. Accounting
> reads `Backup`, which the controller writes.

### 2. The accounting problem

The obvious ledger — sum `status.artifact.sizeBytes` over live `Backup` objects
in the namespace — is wrong in both directions, and this is the part of the
design that deserves review attention.

**Deleting a `Backup` usually does not free storage.** `backup_controller.go`'s
cleanup dispatch is explicit about this, per driver: the Altinity branch notes
that deletion "does NOT purge the upstream clickhouse-backup archive in object
storage"; MariaDB likewise "does not own the archive, so it does not delete it
on CR removal"; FoundationDB says the same of the operator-side CR that drives
a streaming agent. Both branches were written specifically to *avoid* falling
through to the Velero default — which does clean up, and is the one driver for
which deleting a `Backup` genuinely releases bytes.

So the behaviour is not uniform, and it is not incidental: it is a per-driver
property that the code already knows and no other component can see. A tenant
at their quota using any non-Velero driver can `kubectl delete backup --all`,
reclaim their entire allowance, and occupy exactly as many bytes as before.
That is not a leak to be documented — it is a one-command bypass of the feature.

**Driver-side retention frees storage without deleting the CR.** The inverse:
barman or `MaxRetention` prunes an archive on its own schedule while the
`Backup` object remains, so the ledger overcounts and the tenant is charged
quota for bytes that no longer exist.

The two failures share a cause: **the `Backup` CR tracks a record, not an
allocation.** Whether deleting it releases anything depends on which driver
produced it.

#### Artifact ownership as a first-class strategy property

Each strategy declares whether Cozystack owns the artifact's lifecycle:

```go
// StrategyStatus (or the BackupClass resolution result)
type StrategyCapabilities struct {
    // OwnsArtifact is true when deleting the Backup CR also deletes the
    // stored artifact, so the bytes are released with the object.
    OwnsArtifact bool `json:"ownsArtifact"`

    // ReportsSize is true when the driver populates
    // status.artifact.sizeBytes. When false, this strategy's backups are
    // invisible to the size dimension and only count toward the count one.
    ReportsSize bool `json:"reportsSize"`
}
```

This is not new information — it is the per-driver knowledge already encoded as
prose in `backup_controller.go`'s cleanup switch, promoted to a field the
accounting controller can read. Making it explicit also forces each new driver
to answer the question at review time.

Usage then splits in two:

- **Attributed** — bytes of `Backup` objects that exist now. Released when the
  CR is deleted, but only for `OwnsArtifact: true` strategies.
- **Retained** — bytes belonging to deleted CRs whose strategy does *not* own
  the artifact. These stay charged until the driver reports the archive gone.

`used = attributed + retained`. A tenant deleting CRs to dodge the cap moves
bytes from one bucket to the other and frees nothing, which is the correct
answer.

Retained bytes need a reconciliation path or they become permanent. Two
mechanisms, both worth having:

1. **Driver-reported reconciliation.** Where a driver can enumerate its stored
   archives (barman, clickhouse-backup's HTTP API, Velero's backup list), the
   strategy controller periodically reports the true set and the retained
   bucket is recomputed from it. This is the accurate path and should be the
   goal for every driver that can support it.
2. **Explicit release.** An operator (not the tenant) can zero the retained
   bucket for a namespace after purging storage by hand, for drivers with no
   enumeration API.

### 3. Where the numbers live: `corev1.ResourceQuota`

Backup quota uses two new extended dimensions on the tenant's existing
`ResourceQuota`, rather than a new CRD:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: tenant-quota
  namespace: tenant-acme
spec:
  hard:
    requests.cpu: "20"
    services.loadbalancers: "5"
    backups.cozystack.io/size: 500Gi     # total stored bytes
    backups.cozystack.io/count: "200"    # number of Backup objects
status:
  hard:
    ...                                  # kube-controller-manager mirrors spec
    backups.cozystack.io/size: 500Gi
  used:
    requests.cpu: "12"
    backups.cozystack.io/size: 412Gi     # written by the backup quota controller
    backups.cozystack.io/count: "137"
```

Tenants read backup consumption from `kubectl describe resourcequota`, next to
every other limit, and no new API surface is introduced.

The obvious objection is that `ResourceQuota.status` already has two writers —
kube-controller-manager's quota controller and the ResourceQuota admission
plugin — and neither knows what `backups.cozystack.io/size` is. Adding a third
writer sounds like a write war. It is not, and the reason is worth recording
because the whole design rests on it. Reading upstream at `v1.35`:

**kube-controller-manager preserves foreign dimensions.**
`pkg/controller/resourcequota/resource_quota_controller.go`, `syncResourceQuota`:

```go
used := v1.ResourceList{}
if resourceQuota.Status.Used != nil {
    used = quota.Add(v1.ResourceList{}, resourceQuota.Status.Used)  // seed from existing
}
hardLimits := quota.Add(v1.ResourceList{}, resourceQuota.Spec.Hard)

newUsage, err := quota.CalculateUsage(..., hardLimits, rq.registry, ...)
for key, value := range newUsage {
    used[key] = value                                               // overwrite only computed keys
}

hardResources := quota.ResourceNames(hardLimits)
used = quota.Mask(used, hardResources)                              // keep only keys present in spec.hard
```

The sync seeds `used` from what is already published, overwrites only the keys
its own evaluators computed, and masks the result to the key set of
`spec.hard`. A dimension that is in `spec.hard` and in `status.used` but has no
evaluator is therefore carried through untouched. `CalculateUsage`
(`k8s.io/apiserver/pkg/quota/v1/resources.go`) intersects `hard` with the
resources its evaluators match, so an unrecognised name is skipped silently —
it is not an error, and the sync completes normally.

As a bonus, the same function sets `Status.Hard = spec.Hard` wholesale, so
kube-controller-manager publishes the backup *limit* for us. Only `status.used`
needs writing.

**The admission plugin is additive.**
`k8s.io/apiserver/pkg/admission/plugin/resourcequota/controller.go`:

```go
requestedUsage := quota.Mask(deltaUsage, hardResources)
newUsage := quota.Add(resourceQuota.Status.Used, requestedUsage)
...
outQuotas[index].Status.Used = newUsage
```

It adds a delta to the existing `Status.Used` rather than recomputing it, so
foreign keys survive every admission-time write.

**An unpublished backup dimension cannot block unrelated admission.** The
plugin refuses a request when a quota has no usage figure for a dimension it
cares about — but "cares about" is `restrictedResources :=
evaluator.MatchingResources(hardResources)`, computed from the *incoming
object's* evaluator. `hasUsageStats` then skips every resource outside that
set. A Pod create is evaluated by the Pod evaluator, which never matches
`backups.cozystack.io/size`, so a missing or lagging backup figure can never
403 a Pod.

**Consequence: enforcement and visibility are independently switchable.**
`backups.cozystack.io/size` in `spec.hard` is completely inert to
kube-apiserver — no evaluator claims it, so nothing native enforces it. It is a
declared limit that only our webhook acts on. An operator can therefore set the
dimension and get accurate usage reporting with *no* enforcement simply by not
enabling the webhook. That is what makes the phased rollout below real rather
than aspirational.

**Consequence: reporting requires a declared limit.** Because `Mask` drops keys
absent from `spec.hard`, usage cannot be published for a dimension that has no
limit. "Unlimited but measured" is not expressible; an operator who wants
visibility without a meaningful cap must set a deliberately high one.

#### Where the `retained` breakdown goes

`status.used` carries the total that enforcement acts on —
`attributed + retained`. The breakdown does not fit in a `ResourceList` and does
not belong there: it is diagnostic, not a limit. It is exposed as controller
metrics (`cozystack_backup_quota_attributed_bytes`,
`..._retained_bytes`, labelled by namespace) and mirrored into an annotation on
the `ResourceQuota` for `kubectl`-level debugging. An operator asking "why is
this tenant at their cap when they have no backups?" gets the answer from
either.

The tenant chart expresses the limits through the existing `resourceQuotas`
values path, so no new values schema is needed:

```yaml
resourceQuotas:
  backups.cozystack.io/size: 500Gi
  backups.cozystack.io/count: 200
```

Note that `cozy-lib.resources.flatten` currently prefixes unrecognised keys
into `limits.`/`requests.` sections; these two names must be added to its
`$rawQuotaKeys` passthrough list, the same fix pattern as
[cozystack#1636](https://github.com/cozystack/cozystack/issues/1636) applied to
`services.loadbalancers`.

### 4. Enforcement

A validating admission webhook on `backups.cozystack.io/BackupJob` CREATE,
scoped by `namespaceSelector` to tenant namespaces:

```mermaid
sequenceDiagram
    participant T as Tenant / Plan
    participant API as kube-apiserver
    participant W as backup-quota webhook
    participant S as Strategy controller
    participant Q as Backup quota controller

    T->>API: create BackupJob
    API->>W: AdmissionReview
    W->>W: read ResourceQuota status.used + status.hard
    alt used >= hard
        W-->>API: Denied (quota exceeded)
        API-->>T: 403
    else
        W-->>API: Allowed
        API->>S: BackupJob
        S->>S: run driver
        S->>API: create Backup (status.artifact.sizeBytes)
        API->>Q: watch event
        Q->>API: patch ResourceQuota status.used
    end
```

The webhook reads the published `status.used` rather than recomputing from
`Backup` objects, so the ledger has exactly one producer and admission cannot
disagree with what the tenant sees. The cost is staleness, addressed below.

In-flight runs must be counted or a tenant can submit fifty jobs at once and
have them all admitted before any records a `Backup`. The controller therefore
adds non-terminal `BackupJob`s to `count`, and — where the strategy can supply
one — an estimate of their eventual size.

### 5. Overshoot is unbounded, deliberately

Because size is unknown at admission, a namespace at 499 GiB of a 500 GiB cap
can still start a job that writes 5 TB. The gate stops the *next* run, not this
one. This is inherent, and worth stating in tenant-facing docs rather than
hiding: the cap bounds how far a tenant can go *after* being over, not the peak.

Reducing the overshoot is possible but out of scope here: the size of the
application's volumes is knowable at admission and could seed a per-run
estimate. Deferred as a follow-up so that the ledger lands first.

## User-facing changes

- Two new dimensions visible in `kubectl describe resourcequota` — a tenant can
  finally answer "how much backup storage am I using". No new CRD, no new place
  to look.
- New `resourceQuotas` keys in the tenant chart:
  `backups.cozystack.io/size` and `backups.cozystack.io/count`.
- `BackupJob` CREATE can now be rejected with a quota message naming the
  dimension and the figures.
- A denied scheduled run surfaces on `Plan` status and as an event; the
  requester is the controller's service account, so nothing reaches the tenant
  unless we put it there.
- Strategy authors must declare `OwnsArtifact` / `ReportsSize`.

## Upgrade and rollback compatibility

Additive. A `ResourceQuota` without the backup dimensions behaves exactly as
before, so existing clusters are untouched until an operator adds the keys —
the same posture as every other entry in `resourceQuotas` today. The dimensions
are inert to kube-apiserver, so even a cluster that sets them without running
the webhook loses nothing but gains reporting.

The webhook is the one risky component: registering it adds a dependency to
`BackupJob` CREATE that did not exist. Rollback is removing the
`ValidatingWebhookConfiguration` and the CRD; nothing else observes them, and
no `Backup` data is mutated by this proposal at any point.

Existing `Backup` objects predate any ledger. On first reconcile the controller
attributes every live `Backup` it can size and starts `retained` at zero — so
bytes already orphaned before rollout are invisible until the driver-reported
reconciliation path (design §2) covers that driver.

## Security

- **New admission dependency.** A webhook on `BackupJob` CREATE can prevent
  backups from being taken. `failurePolicy` is the whole trade-off: `Fail`
  means an outage of the webhook stops all backups; `Ignore` means it silently
  stops enforcing. Recommended `Fail` with a short timeout, because silently
  not backing up is worse than loudly not backing up — but this is a genuine
  choice and operators should be able to set it.
- **No new tenant-supplied input reaches a privileged path.** The webhook reads
  cluster state; it does not parse the `BackupJob` payload.
- **The controller needs `resourcequotas/status` update** in tenant namespaces.
  That is a privileged subresource shared with kube-controller-manager; the
  grant should be namespace-scoped to tenant namespaces, not cluster-wide on
  all of them, and the controller must never write `spec`.
- **Tenants must not be able to edit `spec.hard`.** This is already true —
  `ResourceQuota` spec is platform-owned in Cozystack — and is precisely why
  reusing it is safer than a new CRD whose RBAC would have to be got right from
  scratch.
- Restores are not gated, deliberately: a tenant over quota must still be able
  to recover.

## Failure and edge cases

- **Driver never sets `sizeBytes`** → those backups contribute 0 to the size
  dimension. Silent under-counting, which is why `ReportsSize` is declared
  rather than inferred: a strategy that does not report size should be visible
  as such in the published figure, not quietly free.
- **Ledger is stale** (controller down, reconciliation behind) → `UsageStale`
  condition; the webhook denies while stale rather than admitting on an
  unknown figure. Fails closed, consistent with `failurePolicy: Fail`.
- **Webhook unavailable** → per `failurePolicy`; with `Fail`, scheduled runs
  fail and retry on the next tick.
- **Tenant deletes `Backup` CRs to free quota** → bytes move to `retained`;
  nothing is freed. The intended behaviour, and the main thing to test.
- **Driver retention prunes an archive** → `retained` (or `attributed`)
  decreases on the next driver-reported reconciliation; until then the tenant
  is over-charged. Bounded by the reconciliation interval.
- **Backup dimensions removed from `spec.hard` while over quota** →
  `Mask` drops them from `status.used` on the next sync and enforcement stops.
  Treated as a deliberate operator action, same as removing any other limit.
- **Two `ResourceQuota` objects both carrying the dimensions** → each is
  evaluated independently and the most restrictive wins, matching native
  `ResourceQuota` semantics. The controller publishes the same `used` figure to
  each.
- **kube-controller-manager syncs from a stale cache** → it seeds `used` from
  the copy it holds, so a just-written backup figure can be briefly rolled back
  to its previous value. Self-healing: the backup quota controller watches
  `ResourceQuota` and re-asserts. Worth stating because it means the controller
  must reconcile continuously, not write once per `Backup` event.
- **Namespace has jobs in flight when a quota is first applied** → they
  complete; the gate applies from the next CREATE.

## Testing

- **Unit** — ledger arithmetic: attribution, the attributed→retained
  transition on CR deletion for a non-owning strategy, release for an owning
  one, multi-object summation, absent-dimension-means-unlimited.
- **Unit** — webhook decision table: under, at, and over each dimension;
  stale ledger; dimension absent from `spec.hard`.
- **Integration (envtest)** — create `Backup` objects, assert published
  `status.used`; delete them, assert the figure does *not* drop for a
  non-owning strategy.
- **Integration, against a real kube-controller-manager** — the assumption §3
  rests on: write `backups.cozystack.io/size` into `status.used`, force a quota
  resync, assert the value survives; then create a Pod in the same namespace and
  assert the admission plugin's write preserves it too. This test is what turns
  "we read the upstream source" into a standing guarantee, and it must fail
  loudly if a future Kubernetes bump changes the behaviour.
- **Integration** — a lagging backup dimension does not block unrelated
  admission: leave `status.used` without the backup keys and assert a Pod
  create still succeeds.
- **E2E** — a tenant with a 1 GiB cap: take backups until denied; delete every
  `Backup` CR; assert the next `BackupJob` is still denied. This is the test
  that proves the feature is not bypassable, and it should exist before the
  feature is called done.
- **E2E** — restore succeeds while over quota.

## Rollout

1. **Accounting controller, no enforcement.** Publishes `status.used` for the
   two dimensions. Because they are inert to kube-apiserver, an operator can
   set a deliberately high limit purely to turn on measurement, size the real
   limit against observed data, and only then move to step 3.
2. **Strategy capability declarations** (`OwnsArtifact`, `ReportsSize`) across
   the shipped drivers, plus driver-reported reconciliation for those that can
   enumerate archives.
3. **Enforcement webhook**, off by default, opt-in per installation.
4. **Tenant chart values** and dashboard surfacing.

Phase 1 is independently useful and carries no risk of refusing a backup, which
argues for shipping it on its own even if the rest stalls in review.

## Open questions

- **How stable is the upstream behaviour §3 depends on?** Foreign-dimension
  preservation is a consequence of how `syncResourceQuota` seeds and masks
  `used`, not a documented API guarantee. It has held for many releases and the
  code reads as deliberate ("preserve the past usage observation"), but a
  refactor upstream could silently drop foreign keys. An e2e test that writes a
  synthetic dimension and asserts it survives a controller resync is cheap
  insurance and should gate the release, not just the merge.
- **Should `count` use the native `count/backups.backups.cozystack.io`
  dimension after all?** It is free and correct for counting, but it fires on
  `Backup` CREATE (the controller), not `BackupJob` CREATE (the tenant) — the
  orphaned-artifact failure in §1. Rejecting it costs a duplicate mechanism;
  accepting it costs a bad failure mode.
- **Where do capability declarations belong** — on the `Strategy` CRs, on
  `BackupClass`, or in a controller-side registry keyed by strategy kind? A
  registry cannot be extended by out-of-tree drivers; a CR field can be set
  wrongly by whoever installs the strategy.
- **Is `retained` per-namespace enough**, or does it need per-strategy
  breakdown to be actionable when an operator has to purge by hand?

## Alternatives considered

**A dedicated `BackupQuota` CRD instead of `ResourceQuota` dimensions.** The
first draft of this proposal took that route, on the assumption that
`ResourceQuota.status` could not be safely co-written. Reading upstream showed
the assumption was wrong (§3), and once foreign dimensions are known to survive,
the CRD only costs: a second place to look for a limit, a second RBAC surface
to get right, a second thing for the dashboard to render, and no reuse of the
`resourceQuotas` values path tenants already use. It would buy a natural home
for the `attributed`/`retained` breakdown — which metrics and an annotation
cover adequately, since that breakdown is diagnostic rather than enforced.

**Sum live `Backup` CRs and accept the bypass.** Far simpler: no ownership
model, no retained bucket, roughly a hundred lines. Rejected because `kubectl
delete backup --all` resets the counter while the bytes remain, which makes the
quota decorative — and a decorative quota is worse than none, since operators
will provision against it.

**Query the storage backend for true usage.** Most accurate, and immune to both
failure directions. Rejected as the primary mechanism: it requires per-backend
credentials and enumeration code in the quota path, couples admission latency
to object-storage availability, and does not generalise across the drivers that
write to operator-managed destinations. It remains the right implementation of
the driver-reported reconciliation in §2 wherever a driver can do it cheaply.

**Native `count/backups.backups.cozystack.io` only, no size dimension.** Free,
zero code. Rejected because backup consumption is a size problem — a hundred
2 GiB backups and a hundred 2 TiB backups are the same number of objects — and
because of the `Backup`-CREATE failure mode in §1.

**Enforce in the strategy controllers instead of admission.** Every driver
checks quota before running. Rejected: the check would be duplicated per driver
and drift, and refusal would surface as a failed run rather than a rejected
request, which is harder for a tenant to distinguish from a real backup
failure.

**A mutating webhook that annotates `BackupJob` with an estimated size.** Would
reduce overshoot, and is a plausible follow-up. Rejected for the first pass
because it needs a per-driver size estimator, and getting the ledger right is
the prerequisite for anything that consumes it.
