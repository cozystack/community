# Tenant host delegation: a per-tenant allowlist and a platform reserved list

- **Title:** Tenant host delegation: a per-tenant allowlist and a platform reserved list
- **Author(s):** @mattia-eleuteri
- **Date:** 2026-09-07
- **Status:** Draft

## Overview

`Tenant.spec.host` is today writable only by `system:masters` or a `cozy-*` service account. That
gate exists for a good reason — the value becomes the namespace's `namespace.cozystack.io/host`
label, which the Gateway and Route admission policies trust as the tenant's apex, and the Ingress
policy trusts under the platform root apex — but it makes bring-your-own-domain impractical on a
multi-tenant platform: every hostname a tenant wants is a manual cluster-admin operation, and the
API has no way to express "this domain belongs to that tenant".

This proposes replacing the identity check with a decision the platform can delegate: a **per-tenant
allowlist**, inherited by subtenants, saying which hostnames a tenant may use, plus a **platform
reserved list** that no allowlist can override. Writing the allowlist is itself gated, so a tenant
cannot grant itself a domain. Once delegation is on, the same allowlist also bounds the one path
that today admits an external hostname without consulting anything: the Ingress policy's branch
for names outside the platform root apex. Cozystack applies the verdict; whoever runs the platform
decides what goes in the allowlist, and may delegate that decision to a control panel that performs
its own domain-ownership verification.

## Scope and related proposals

- [`external-database-exposure`](../external-database-exposure/README.md) documents that the apex
  reaching a `TenantGateway` from the `namespace.cozystack.io/host` label is "normalised by
  nothing", so an apex carrying upper case leaves its listener hostnames unsatisfiable. This
  proposal's admission checks normalise case for the same reason (§Security), and the two share
  the assumption that the label is the tenant's authoritative apex.
- Out of scope here, and deliberately so: **how** ownership of a domain is established. DNS
  challenge flows, a verification state machine, and any operator-facing UI for granting a domain
  belong to whatever component performs the verification, not to Cozystack admission.

## Decisions

<!-- Empty in the initial PR; records land in ./decisions/ as implementation proceeds. -->

## Context

Hostname safety in Cozystack rests on a layered set of `ValidatingAdmissionPolicy` objects
rendered by `packages/system/cozystack-basics/templates/`, documented in
`packages/extra/gateway/README.md` under "Security model". The layer relevant here is layer 4,
`cozystack-tenant-host-policy` in `gateway-hostname-policy.yaml`: it rejects any set or change of
`Tenant.spec.host` unless the caller's groups contain `system:masters`,
`system:serviceaccounts:cozy-system`, `system:serviceaccounts:cozy-cert-manager`,
`system:serviceaccounts:cozy-fluxcd` or `system:serviceaccounts:kube-system`.

The value matters because of what consumes it. `packages/apps/tenant/templates/namespace.yaml`
writes it to the namespace's `namespace.cozystack.io/host` label. Layers 2 and 7 —
`cozystack-gateway-hostname-policy` and `cozystack-route-hostname-policy` — constrain Gateway
listeners and `HTTPRoute`/`TLSRoute`/`GRPCRoute` hostnames to that apex. Layer 8,
`cozystack-ingress-hostname-policy` in `ingress-hostname-policy.yaml`, does so only under the
platform root apex (`_cluster.root-host`): it admits an `Ingress` host that lies within the
namespace's apex, **or** any concrete, non-wildcard name lying entirely outside the root apex, with
no reference to the label. A tenant that could set `spec.host` freely could name someone else's
domain and then obtain routes and an ACME certificate for it. Layer 5,
`cozystack-namespace-host-label-policy`, makes the label itself immutable to the same trusted set
so the label cannot be written directly.

Layer 8's outside-root branch is reachable by a tenant today, and on the default path. The
`kubernetes` app writes `addons.ingressNginx.hosts` as is into an `Ingress` on the shared
ingress-nginx when `exposeMethod` is `Proxied` (`packages/apps/kubernetes/templates/ingress.yaml`);
the branch exists precisely so that a user-supplied domain can be routed to a nested cluster. A
tenant can therefore already publish an external domain — its own or someone else's — without
touching `spec.host`. Gating `spec.host` alone would leave the API's account of a tenant's
hostnames true on the opt-in Gateway path and false on the default Ingress path; §5 closes that
branch behind the same flag.

Layer 4 is therefore correct in intent. What it lacks is any input other than the caller's
identity, and what layer 8 lacks outside the root apex is any bound at all.

### The problem

A tenant user cannot create a subtenant with a hostname:

> `tenants.apps.cozystack.io "acme" is forbidden: ValidatingAdmissionPolicy`
> `'cozystack-tenant-host-policy' denied request: tenant.spec.host can only be set or changed by`
> `cluster-admins (system:masters) or by cozystack/Flux service accounts`

For a tenant user, no non-empty value is reachable. Their own delegated subdomain is refused
upstream, by the denylist of the control panel that fronts Cozystack on such a platform — a
component outside Cozystack, which itself keeps no such list; their own external domain is refused
by this policy;
only an empty `host` works, which silently inherits the parent apex. Two models are in force at
once — "platform subdomains belong to the platform, bring your own domain" and "only a
cluster-admin assigns a domain" — and their intersection is empty.

The practical consequence is that self-service domain configuration is impossible: each hostname
becomes a support ticket, and a control panel that has already verified DNS ownership has no way
to record that fact in the API.

## Goals

- A tenant-super-admin can set `spec.host` to a hostname the platform has delegated to that
  tenant, with no cluster-admin involvement.
- Whoever runs the platform keeps an unconditional veto over hostnames they consider theirs, which
  no delegation can override.
- The set of hostnames a tenant may use is expressed **in the API**, readable and auditable, not
  implied by an external system's state — on the `spec.host`, Gateway and default Ingress paths
  alike, once delegation is on.
- A tenant cannot widen its own allowlist.
- A cluster with default values behaves exactly as it does today, so the change is adoptable
  without a migration.
- Delegation is inherited, so a tenant granted a domain can use it across its whole subtree without
  a separate grant per subtenant.

### Non-goals

- Proving ownership of a domain. Cozystack applies a verdict; it does not verify DNS.
- Resolving hostname collisions between tenants under a shared apex. The controller already
  reports that as a `HostnameConflict` condition and this proposal does not change it.
- Revoking a hostname already in use. The policies run on write, so an existing `spec.host` or
  `Ingress` host survives the removal of the allowlist entry that permitted it. Same as every other
  admission gate here.
- Changing layers 1, 2, 3, 6 or 7. Layer 8 changes only behind `tenantHostDelegation` (§5); with
  the flag off it renders as today.

## Design

### 1. Where the allowlist lives

A tenant's effective allowlist rides on an annotation on its namespace:

```yaml
namespace.cozystack.io/allowed-hosts: "example.net corp.example.org"
```

An annotation rather than a label because label values cap at 63 characters and admit no comma; a
list of domains does not fit. Entries are space-separated.

`packages/apps/tenant/templates/namespace.yaml` computes it per tenant namespace, deduplicated, as:

```
allowed-hosts(T) = inherited(T) ∪ { "." + computedHost(T) }
inherited(T)     = inherited(parent) ∪ T.spec.allowedHosts
```

and propagates `inherited(T)` to children through the existing `_namespace` channel in the
`cozystack-values` Secret, alongside `host`, `etcd` and `gateway`. Inheritance therefore needs no
new mechanism.

Seeding the tenant's own apex — the second term — is what lets a tenant's subtenants use hostnames
under the domain already delegated to it. It is gated on a platform flag, because switching it on
grants every tenant a capability it did not have.

The seeded entry is **strict-subdomain** (the leading dot, §2) and it is **not propagated**: each
tenant's annotation seeds its own apex and no ancestor's. Both are needed to keep a child off its
parent's exact apex. Derived apexes nest — `team.acme.example.org` under `acme.example.org` — so a
strict entry alone, if inherited, would still let a grandchild claim `team.acme.example.org`, its
parent's apex, through the ancestor's `.acme.example.org`. Without propagation, a tenant's children
reach subdomains of their parent's apex and the explicit grants, and nothing else under an
ancestor's apex — an uncle's subtree, for one. Two siblings claiming the same name under their
parent's apex remain the `HostnameConflict` case of §Non-goals.

`tenant-root` is the exception, because the tenant chart skips it: its namespace is rendered by
`packages/system/cozystack-basics/templates/tenant-root.yaml`, and its children's `_namespace` by
`cozystack-values-secret.yaml` in the same chart. There the annotation is rendered from a platform
value, `gateway.rootAllowedHosts` (§7):

```
allowed-hosts(tenant-root) = gateway.rootAllowedHosts
inherited(tenant-root)     = ∅
```

Its entries are explicit and apex-inclusive, as any grant. Two things are deliberately absent:

- **No seeded entry.** Seeding `.<root-host>` would hand every level-1 tenant any subdomain of the
  platform apex — `dashboard.`, `keycloak.` — which is the hijack layer 4 exists to prevent.
- **No propagation.** The root's subtree is the whole platform, so an inherited root entry would be
  claimable by every tenant at every depth. A level-1 tenant that needs a domain further down gets
  it through its own `spec.allowedHosts`, like any other tenant.

The value has two readers. Layer 8 reads it for `Ingress` objects in `tenant-root` itself (§5), and
layer 4 reads it for `spec.host` on level-1 tenants, whose `Tenant` objects live in `tenant-root`.
The latter is level-1 BYOD through an explicitly named platform value rather than a blanket
annotation. `spec.allowedHosts` on `Tenant/root` stays inert; the field's description points to
the platform value instead.

### 2. The new field

```yaml
## @param {[]string} [allowedHosts] - Hostnames this tenant's subtenants may use for `host`, and this tenant for Ingress hosts outside the platform apex …
allowedHosts: []
```

Declared in `packages/apps/tenant/values.yaml`, which is the source of truth;
`api/apps/v1alpha1/tenant/types.go`, `values.schema.json`, the chart README and the
`ApplicationDefinition` schema are generated from it.

An explicit entry covers the hostname itself and any subdomain of it:
`host == entry || host.endsWith("." + entry)`. Explicit grants stay apex-inclusive because the
grantee must be able to use exactly the domain it was granted. The seeded apex is the one strict
entry: the chart writes it with a leading dot, and for an entry starting with `.` membership is
`host.endsWith(entry)` — one condition. Layer 9 rejects a leading dot in `spec.allowedHosts`, so only
the chart can write a strict entry. **There is no wildcard syntax** — no `*.` parsing is
introduced, and `endsWith("." + entry)` is what makes `notexample.net` fail against a grant of
`example.net`.

### 3. Layer 4, rewritten

`cozystack-tenant-host-policy` keeps its name, `matchConstraints`, `matchConditions` and binding.
Its decision becomes, in order:

```
ALLOW if the caller is trusted          ← unchanged escape hatch, evaluated FIRST
ALLOW if spec.host is being cleared     ← a strict reduction of privilege
DENY  if host ∈ reservedHosts           ← the platform's veto
ALLOW if host ∈ allowlist(namespaceObject)
DENY
```

The order is significant: trusted callers are checked before the reserved list, because setting a
reserved zone on `tenant-root` is precisely a cluster-admin's job.

The allowlist is read from `namespaceObject` — the namespace the `Tenant` object lives in, which is
the **parent's** namespace. A grant on tenant T therefore governs T's children, not T itself. That
is a consequence of where the CR lives, and it is stated in the field's own description.

The rewritten message no longer interpolates `request.userInfo.username`: the verdict no longer
depends on the caller's identity, and the old message put user emails into error payloads.

### 4. Layer 9: gating the grant

A new policy, `cozystack-tenant-allowed-hosts-policy`, restricts writes to `spec.allowedHosts`.
Without it the design would be self-signed: Cozystack hands each tenant a kubeconfig by design
(`packages/extra/info/templates/kubeconfig.yaml`) and `cozy:tenant:super-admin:base` carries
`apps.cozystack.io/*`, so a tenant could write its own allowlist and then use it.

It carries three validations: the grant may only be written by the grant set; only a true
`system:masters` may place a reserved zone into an allowlist (defence in depth — layer 4 would
catch the host anyway, but at the wrong step and with a worse message); and each entry must be a
single hostname with no whitespace and no leading dot, because the annotation is space-separated —
an entry containing a space would split into two once written — and a leading dot is the chart's
marker for the strict seeded entry (§2).

### 5. Layer 8: bounding the outside-root branch

`cozystack-ingress-hostname-policy` keeps its in-apex branch, its anti-wildcard gate, and its checks
on `spec.rules[].host`, `spec.tls[].hosts[]` and `spec.defaultBackend`. When
`gateway.tenantHostDelegation` is on, its outside-root branch gains two conditions; when it is off,
the template renders today's expression unchanged.

```
ALLOW if host is within the namespace apex            ← unchanged
ALLOW if host is concrete and outside the root apex   ← unchanged, and now also:
      AND host ∈ allowlist(namespaceObject)
      AND host ∉ reservedHosts
DENY
```

Here `namespaceObject` is the namespace the `Ingress` lives in — the tenant's own, not its parent's
as in layer 4 — so the annotation that applies is the tenant's own: what its ancestors granted plus
what was granted on it. A domain granted on T is thus usable by an `Ingress` in T and by `spec.host`
on T's children, which is one subtree. The seeded entry adds nothing here, since every host under
the tenant's apex already passes the in-apex branch.

The reserved check mirrors layer 4, where the veto wins over the allowlist. Layer 8 matches only
`tenant-*` namespaces, so it has no trusted-caller branch to order the veto after. An absent
annotation resolves to an empty allowlist and the branch denies, fail-closed like the label check
the policy already carries. Both operands are lowercased, as the existing branches do.

This also bounds the `kubernetes` app's `Proxied` ingress: its `addons.ingressNginx.hosts` outside
the root apex must now be granted to the tenant, which is the point — and the one migration step
(§Upgrade and rollback compatibility).

`tenant-root` is in scope too, since the policy matches every `tenant-*` namespace. Its annotation
comes from `gateway.rootAllowedHosts` (§1), so a `kubernetes` app in `Proxied` mode or a Harbor with
a custom `host` living in `tenant-root` keeps its external domain by listing it there. Without
that value the root's allowlist is empty and those hosts are denied on their next write.

### 6. Two trust sets, deliberately disjoint

| Set | Members | May |
|---|---|---|
| **Grant** | `system:masters`, the `cozy-*` service accounts, plus `gateway.hostGrantGroups` | write `spec.allowedHosts` |
| **Host** | `system:masters`, the `cozy-*` service accounts | write `spec.host` directly |

A group named in `hostGrantGroups` can delegate a domain but never set `spec.host` itself. That is
what makes the reserved list load-bearing rather than decorative: a bad grant produced by a buggy
ownership check still cannot reach a reserved zone.

The dependency runs that way round, and it is worth stating because it is easy to invert: the
reserved list is what keeps the disjointness meaningful, not the reverse. With `reservedHosts`
empty, a grant account can grant `example.org` to a tenant whose subtenant then claims
`dashboard.example.org`. `hostGrantGroups` should not be populated unless `reservedHosts` covers
the platform's own hostnames.

### 7. Platform values

Four keys under `gateway:` in `packages/core/platform/values.yaml`, reaching the charts through
the existing `_cluster` channel:

| Value | `_cluster` key | Consumer |
|---|---|---|
| `gateway.reservedHosts` | `gateway-reserved-hosts` | layers 4, 8 and 9 |
| `gateway.hostGrantGroups` | `gateway-host-grant-groups` | layer 9 |
| `gateway.tenantHostDelegation` | `gateway-tenant-host-delegation` | the tenant chart, layer 8 |
| `gateway.rootAllowedHosts` | `gateway-root-allowed-hosts` | `tenant-root.yaml` |

`rootAllowedHosts` never passes through admission on its way in, so `tenant-root.yaml` applies
layer 9's entry rules at render time: it fails the render on an entry that contains whitespace,
starts with a dot, or falls under `reservedHosts`.

`hostGrantGroups` takes Kubernetes **group** names (e.g. `system:serviceaccounts:my-api`), because
the policy compares against `request.userInfo.groups`. A namespace-wide group trusts every service
account in that namespace, present and future — a blast radius worth stating rather than implying.

Rendered into the CEL from values rather than carried by a `paramRef`: all existing Cozystack
policies are rendered templates and none uses `paramKind`, so introducing that pattern would add a
review surface for little gain on a list that changes rarely.

## User-facing changes

- **Tenants** gain a read-only view of `spec.allowedHosts` on their own `Tenant`, and the ability
  to set `spec.host` to a hostname under an entry of it. The denial message names what is wrong
  instead of naming who they are. With delegation on, an external domain on the default Ingress
  path — the `kubernetes` app's `Proxied` hosts, for one — must also be in their allowlist.
- **Platform operators** gain four `gateway.*` values. Off by default.
- **Docs:** `packages/extra/gateway/README.md` gains layer 9 and rewritten layers 4 and 8;
  `content/en/docs/next/operations/configuration/platform-package.md` in `cozystack/website` needs
  the four new keys.

## Upgrade and rollback compatibility

At default values — `reservedHosts: []`, `hostGrantGroups: []`, `rootAllowedHosts: []`,
`tenantHostDelegation: false` — an existing cluster behaves identically:

- Both reserved checks render as empty CEL list literals and evaluate false unconditionally.
- Every tenant's allowlist annotation renders empty, `tenant-root`'s included, and an empty-entry
  guard keeps an empty annotation from matching anything.
- The trusted-caller set is untouched, so the only passing branch remains the one that passes today.
- Layer 8 renders today's expression: the outside-root conditions are emitted only when
  `tenantHostDelegation` is true.

No migration at default values. Existing `Tenant` objects gain no field value, and existing
namespaces gain one empty annotation, which nothing reads unless the feature is on.

Rollback is reverting the charts: the annotation becomes inert, and any `spec.host` already
accepted survives, because the policy runs on write.

Once `tenantHostDelegation: true`, the defensible claim is narrower than full compatibility and
should be stated as such: *no tenant reaches a host outside a zone a trusted caller already
delegated to it* — through `spec.host`, a Gateway route or an `Ingress`. Tenants do gain a
capability — that is the feature — and lose one: an external domain on the default Ingress path,
today admitted unconditionally, now needs a grant.

That loss is the one migration step. An `Ingress` already admitted survives, because the policy
runs on write, but the next write to it — an upgrade of the `kubernetes` app, an edit of
`addons.ingressNginx.hosts` — is denied if its host is not granted, and the HelmRelease fails.
Before enabling the flag, an operator lists the `Ingress` hosts in `tenant-*` namespaces that fall
outside the root apex and grants each on the tenant that owns it, or on an ancestor — or, for
`tenant-root`, which has neither, in `gateway.rootAllowedHosts`. Cozystack
cannot do this step for them: it has no way to tell a legitimate external domain from a squatted
one, which is why the branch needs a grant in the first place.

## Security

- **New tenant-supplied input:** none. `spec.host` was already tenant-supplied and remains gated;
  `spec.allowedHosts` is writable only by the grant set.
- **New RBAC surface:** none. No new verbs, no new resources in any `cozy:tenant:*` role.
- **New trust boundary:** `gateway.hostGrantGroups`. Populating it delegates domain assignment to
  another component, bounded by `reservedHosts` (§6).
- **Case normalisation is load-bearing.** Kubernetes permits upper case in a label value, and the
  `namespace.cozystack.io/host` label is normalised by nothing on the way in — a point
  [`external-database-exposure`](../external-database-exposure/README.md) documents independently.
  Every hostname comparison here therefore lowercases **both** operands, as layers 2, 7 and 8
  already do. Without it, a tenant granted `example.com` could claim `svc.INTERNAL.example.com`
  past a reserved `internal.example.com`, and since the downstream consumers do normalise, an
  all-lowercase route would then match the label.
- **Encoding integrity.** The annotation is space-separated, so an `allowedHosts` entry or a
  `spec.host` containing whitespace would split into two entries downstream and grant a domain
  nobody granted. Both fields reject whitespace at admission.
- **The annotation is protected.** Layer 5 is extended to make
  `namespace.cozystack.io/allowed-hosts` immutable to the same trusted set as the host label. The
  annotation is strictly more powerful than the label — it governs future writes rather than
  recording one committed value — so leaving it unguarded while guarding the label would be
  incoherent. Tenants hold no `namespaces` RBAC today, so this is defence in depth.

## Failure and edge cases

- `spec.host` outside every allowlist entry → denied, message names the constraint.
- `spec.host` under an allowlist entry **and** under a reserved entry → denied; the veto wins.
- Cluster-admin sets a reserved host on `tenant-root` → allowed; trusted callers precede the veto.
- Grant written by a tenant → denied by layer 9, naming `allowedHosts`.
- Grant containing a reserved zone, written by a grant account → denied; only `system:masters` may.
- `spec.host` cleared (non-empty → empty) → allowed for any caller; the tenant falls back to the
  derived apex it already owns.
- `spec.host` equal to the parent's exact apex, delegation on → denied: the seeded entry is
  strict-subdomain and no ancestor's seeded entry is inherited (§1).
- `Ingress` host outside the root apex and outside the tenant's allowlist, delegation on → denied
  by layer 8. The same host with delegation off → admitted, as today.
- `Ingress` host outside the root apex, granted **and** under a reserved entry → denied; the veto
  wins on this path too.
- A stored value that already contains whitespace → the whitespace checks are gated on the field
  actually changing, so a pre-existing bad value does not retroactively block unrelated updates to
  the object.
- Parent's annotation absent (e.g. a namespace the tenant chart has not reconciled) → the allowlist
  resolves empty and the write is denied. Fail-closed.
- `Ingress` in `tenant-root` with a host outside the root apex, delegation on → admitted only if
  the host is in `gateway.rootAllowedHosts`.
- Level-1 tenant's `spec.host` set by a tenant user → admitted only if under an entry of
  `gateway.rootAllowedHosts`, and never under a reserved entry. Empty by default, so denied.
- `spec.allowedHosts` on `Tenant/root` → no effect; the root's grant is `gateway.rootAllowedHosts`
  (§1).
- `gateway.rootAllowedHosts` entry with whitespace, a leading dot, or under `reservedHosts` → the
  `cozystack-basics` render fails, so the bad value never reaches the cluster.

## Testing

- **helm-unittest**, `packages/system/cozystack-basics`: both policies render, the reserved and
  grant lists are injected from `_cluster`, the bindings are `validationActions: [Deny]`, and —
  because a loose assertion here is worse than none — the rule *order* is pinned by anchoring on
  the literal disjunction rather than searching for the operand names, so a variant that AND-gates
  the trusted caller behind the veto fails the suite. Layer 8 renders today's expression with the
  flag off, and with it on carries the allowlist and reserved conditions inside the outside-root
  branch only. `tenant-root` carries the annotation from `rootAllowedHosts`, empty by default and
  with no seeded entry; the root's `cozystack-values` Secret propagates no allowlist; the render
  fails on a malformed or reserved `rootAllowedHosts` entry.
- **helm-unittest**, `packages/apps/tenant`: the annotation is empty by default, seeds the apex
  with a leading dot only when delegation is on, unions inherited ∪ explicit grants ∪ seeded apex
  with deduplication, and propagates through `_namespace` without the seeded entry.
- **Chainsaw e2e**, `hack/e2e-chainsaw/gateway/`: a two-level fixture — the parent tenant carries
  the grants, a child performs the `spec.host` writes, because that is the direction inheritance
  actually runs. Cases: annotation carries the effective list; a tenant cannot self-grant; a covered
  host is accepted; an uncovered host is denied; the parent's exact apex is denied; and a reserved
  host is denied *while granted*, that last one gated on the grant having actually propagated so it
  can only fail because of the veto. On the Ingress path, an `Ingress` in the child with an external
  host is denied until that host is granted, then accepted; the same in `tenant-root`, granted
  through `rootAllowedHosts`.
- **CEL evaluation** of the rendered expressions, as a check that the policies mean what the tests
  assert about their text.

## Rollout

One release. All four values default to inert, so the charts can ship ahead of any platform
enabling them. A platform enables in three steps: `reservedHosts` first, to install the veto before
granting anything; then grants for the external domains already in use on the Ingress path, with
`rootAllowedHosts` for those in `tenant-root` (§Upgrade and rollback compatibility); then
`tenantHostDelegation`. Nothing is deprecated.

## Open questions

1. **Reserved grants under GitOps.** Only `system:masters` may place a reserved zone in an
   allowlist, and Flux applies as `cozy-fluxcd` — so such a grant cannot be declared in GitOps and
   would be re-denied on every reconcile. Accept the limitation, or admit the `cozy-*` accounts
   there and rely on layer 4 alone for the veto?
2. **Scoping a grant.** A grant is inherited by the entire subtree, with no way to scope it to one
   branch or narrow an inherited entry. Is per-branch scoping worth a mechanism?

## Alternatives considered

**Carry the allowlist in a `paramRef` instead of rendering it into the CEL.** More dynamic — the
list becomes a cluster object rather than a values change plus a reconcile. Rejected because no
existing Cozystack policy uses `paramKind`, so it adds a review surface and a new operational
object for a list that changes rarely.

**Move the validation into `cozystack-api` instead of a VAP.** `Tenant` is served by an aggregated
apiserver, so a Go validator is available. Rejected as more intrusive than the layered VAP model
already in place, and it would put the hostname rules in a different place from layers 2, 5, 7 and 8.

**Let the tenant write its own allowlist.** Simplest possible change. Rejected because tenants get
a kubeconfig and `apps.cozystack.io/*` in their own namespace by design, so the allowlist would be
self-signed and any external verification trivially bypassable.

**A label instead of an annotation.** Rejected on mechanics: 63 characters, no commas.

**Leave layer 8 alone and scope the allowlist to `spec.host` and the Gateway path.** Smaller, and
no migration step. Rejected because the default Ingress path would keep admitting any external
domain, so the API's account of what a tenant may use would hold only on the opt-in path — the
goal would be met where it matters least.

**Leave `tenant-root` out of §5.** No new value. Rejected because `tenant-root` would then be the
one namespace whose external Ingress hosts the API does not account for, and level-1 BYOD would
stay a trusted-caller operation; one platform value closes both.

**Seed or propagate the root's allowlist like any tenant's.** Rejected: seeding hands every level-1
tenant the platform apex, and propagating makes every root entry claimable by every tenant (§1).

**An apex-inclusive seeded entry.** What the first draft had. It lets a child claim its parent's
exact apex: no trust boundary is crossed, since only the parent can create that child, but two
namespaces end up with one host label and a `HostnameConflict` with no defined winner. A strict
entry costs one condition (§2).

**Seed `reservedHosts` implicitly from `_cluster.root-host`,** so the default configuration protects
the platform apex without the operator enumerating `dashboard.`, `keycloak.` and friends. Rejected
after evaluation: every derived tenant apex is a subdomain of the platform apex, so reserving it
denies each tenant the very apex just delegated to it — it breaks the feature rather than hardening
it.

**A single trust set for both fields.** Rejected: it collapses the distinction that makes the
reserved list meaningful, and it would let a domain-granting component set hostnames directly.
