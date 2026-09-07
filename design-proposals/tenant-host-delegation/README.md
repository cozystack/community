# Tenant host delegation: a per-tenant allowlist and a platform reserved list

- **Title:** Tenant host delegation: a per-tenant allowlist and a platform reserved list
- **Author(s):** @mattia-eleuteri
- **Date:** 2026-09-07
- **Status:** Draft

## Overview

`Tenant.spec.host` is today writable only by `system:masters` or a `cozy-*` service account. That
gate exists for a good reason — the value becomes the namespace's `namespace.cozystack.io/host`
label, which the Gateway, Route and Ingress admission policies all trust as the tenant's apex — but
it makes bring-your-own-domain impractical on a multi-tenant platform: every hostname a tenant
wants is a manual cluster-admin operation, and the API has no way to express "this domain belongs
to that tenant".

This proposes replacing the identity check with a decision the platform can delegate: a **per-tenant
allowlist**, inherited by subtenants, saying which hostnames a tenant may use, plus a **platform
reserved list** that no allowlist can override. Writing the allowlist is itself gated, so a tenant
cannot grant itself a domain. Cozystack applies the verdict; whoever runs the platform decides what
goes in the allowlist, and may delegate that decision to a control panel that performs its own
domain-ownership verification.

## Scope and related proposals

- [`external-database-exposure`](../external-database-exposure/README.md) documents that the apex
  reaching a `TenantGateway` from the `namespace.cozystack.io/host` label is "normalised by
  nothing", so an apex carrying upper case leaves its listener hostnames unsatisfiable. This
  proposal's admission checks normalise case for the same reason (§Security), and the two share
  the assumption that the label is the tenant's authoritative apex.
- Out of scope here, and deliberately so: **how** ownership of a domain is established. DNS
  challenge flows, a verification state machine, and any operator-facing UI for granting a domain
  belong to whatever component performs the verification, not to Cozystack admission.
- Also out of scope: BYOD for level-1 tenants, meaning direct children of `Tenant/root` — see
  §Open questions.

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
writes it to the namespace's `namespace.cozystack.io/host` label, and layers 2, 7 and 8 —
`cozystack-gateway-hostname-policy`, `cozystack-route-hostname-policy` and
`cozystack-ingress-hostname-policy` — all constrain Gateway listeners, `HTTPRoute`/`TLSRoute`/
`GRPCRoute` hostnames and `Ingress` hosts against it. A tenant that could set `spec.host` freely
could name someone else's domain and then obtain routes and an ACME certificate for it. Layer 5,
`cozystack-namespace-host-label-policy`, makes the label itself immutable to the same trusted set
so the label cannot be written directly.

The gate is therefore correct in intent. What it lacks is any input other than the caller's
identity.

### The problem

A tenant user cannot create a subtenant with a hostname:

> `tenants.apps.cozystack.io "acme" is forbidden: ValidatingAdmissionPolicy`
> `'cozystack-tenant-host-policy' denied request: tenant.spec.host can only be set or changed by`
> `cluster-admins (system:masters) or by cozystack/Flux service accounts`

For a tenant user, no non-empty value is reachable. Their own delegated subdomain is refused by the
platform's own denylist in its API layer; their own external domain is refused by this policy;
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
  implied by an external system's state.
- A tenant cannot widen its own allowlist.
- A cluster with default values behaves exactly as it does today, so the change is adoptable
  without a migration.
- Delegation is inherited, so a tenant granted a domain can use it across its whole subtree without
  a separate grant per subtenant.

### Non-goals

- Proving ownership of a domain. Cozystack applies a verdict; it does not verify DNS.
- Resolving hostname collisions between tenants under a shared apex. The controller already
  reports that as a `HostnameConflict` condition and this proposal does not change it.
- Revoking a hostname already in use. The policy runs on write, so an existing `spec.host` survives
  the removal of the allowlist entry that permitted it. Same as every other admission gate here.
- Changing layers 1, 2, 3, 6, 7 or 8.

## Design

### 1. Where the allowlist lives

A tenant's effective allowlist rides on an annotation on its namespace:

```yaml
namespace.cozystack.io/allowed-hosts: "example.net corp.example.org"
```

An annotation rather than a label because label values cap at 63 characters and admit no comma; a
list of domains does not fit. Entries are space-separated.

`packages/apps/tenant/templates/namespace.yaml` computes it per tenant namespace as the union of
three sources, deduplicated, in this order:

```
allowed-hosts(T) = allowed-hosts(parent) ∪ { computedHost(T) } ∪ T.spec.allowedHosts
```

and propagates it to children through the existing `_namespace` channel in the `cozystack-values`
Secret, alongside `host`, `etcd` and `gateway`. Inheritance therefore needs no new mechanism.

Seeding the tenant's own apex — the middle term — is what lets a tenant use hostnames under the
domain already delegated to it. It is gated on a platform flag, because switching it on grants
every tenant a capability it did not have.

### 2. The new field

```yaml
## @param {[]string} [allowedHosts] - Hostnames this tenant's subtenants may use for `host` …
allowedHosts: []
```

Declared in `packages/apps/tenant/values.yaml`, which is the source of truth;
`api/apps/v1alpha1/tenant/types.go`, `values.schema.json`, the chart README and the
`ApplicationDefinition` schema are generated from it.

An entry covers the hostname itself and any subdomain of it. Membership is always
`host == entry || host.endsWith("." + entry)`. **There is no wildcard syntax** — no `*.` parsing is
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
single hostname with no whitespace, because the annotation is space-separated and an entry
containing a space would split into two once written.

### 5. Two trust sets, deliberately disjoint

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

### 6. Platform values

Three keys under `gateway:` in `packages/core/platform/values.yaml`, reaching the charts through
the existing `_cluster` channel:

| Value | `_cluster` key | Consumer |
|---|---|---|
| `gateway.reservedHosts` | `gateway-reserved-hosts` | layers 4 and 9 |
| `gateway.hostGrantGroups` | `gateway-host-grant-groups` | layer 9 |
| `gateway.tenantHostDelegation` | `gateway-tenant-host-delegation` | the tenant chart |

`hostGrantGroups` takes Kubernetes **group** names (e.g. `system:serviceaccounts:my-api`), because
the policy compares against `request.userInfo.groups`. A namespace-wide group trusts every service
account in that namespace, present and future — a blast radius worth stating rather than implying.

Rendered into the CEL from values rather than carried by a `paramRef`: all existing Cozystack
policies are rendered templates and none uses `paramKind`, so introducing that pattern would add a
review surface for little gain on a list that changes rarely.

## User-facing changes

- **Tenants** gain a read-only view of `spec.allowedHosts` on their own `Tenant`, and the ability
  to set `spec.host` to a hostname under an entry of it. The denial message names what is wrong
  instead of naming who they are.
- **Platform operators** gain three `gateway.*` values. Off by default.
- **Docs:** `packages/extra/gateway/README.md` gains layer 9 and a rewritten layer 4;
  `content/en/docs/next/operations/configuration/platform-package.md` in `cozystack/website` needs
  the three new keys.

## Upgrade and rollback compatibility

At default values — `reservedHosts: []`, `hostGrantGroups: []`, `tenantHostDelegation: false` — an
existing cluster behaves identically:

- Both reserved checks render as empty CEL list literals and evaluate false unconditionally.
- Every tenant's allowlist annotation renders empty, and an empty-entry guard keeps an empty
  annotation from matching anything.
- The trusted-caller set is untouched, so the only passing branch remains the one that passes today.

No migration. Existing `Tenant` objects gain no field value, and existing namespaces gain one empty
annotation, which nothing reads unless the feature is on.

Rollback is reverting the charts: the annotation becomes inert, and any `spec.host` already
accepted survives, because the policy runs on write.

Once `tenantHostDelegation: true`, the defensible claim is narrower than full compatibility and
should be stated as such: *no tenant reaches a host outside a zone a trusted caller already
delegated to it.* Tenants do gain a capability — that is the feature.

## Security

- **New tenant-supplied input:** none. `spec.host` was already tenant-supplied and remains gated;
  `spec.allowedHosts` is writable only by the grant set.
- **New RBAC surface:** none. No new verbs, no new resources in any `cozy:tenant:*` role.
- **New trust boundary:** `gateway.hostGrantGroups`. Populating it delegates domain assignment to
  another component, bounded by `reservedHosts` (§5).
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
- A stored value that already contains whitespace → the whitespace checks are gated on the field
  actually changing, so a pre-existing bad value does not retroactively block unrelated updates to
  the object.
- Parent's annotation absent (e.g. a namespace the tenant chart has not reconciled) → the allowlist
  resolves empty and the write is denied. Fail-closed.
- `spec.allowedHosts` on `Tenant/root` → no effect; see §Open questions.

## Testing

- **helm-unittest**, `packages/system/cozystack-basics`: both policies render, the reserved and
  grant lists are injected from `_cluster`, the bindings are `validationActions: [Deny]`, and —
  because a loose assertion here is worse than none — the rule *order* is pinned by anchoring on
  the literal disjunction rather than searching for the operand names, so a variant that AND-gates
  the trusted caller behind the veto fails the suite.
- **helm-unittest**, `packages/apps/tenant`: the annotation is empty by default, seeds the apex only
  when delegation is on, unions parent ∪ apex ∪ explicit grants with deduplication, and propagates
  through `_namespace`.
- **Chainsaw e2e**, `hack/e2e-chainsaw/gateway/`: a two-level fixture — the parent tenant carries
  the grants, a child performs the `spec.host` writes, because that is the direction inheritance
  actually runs. Cases: annotation carries the effective list; a tenant cannot self-grant; a covered
  host is accepted; an uncovered host is denied; and a reserved host is denied *while granted*, that
  last one gated on the grant having actually propagated so it can only fail because of the veto.
- **CEL evaluation** of the rendered expressions, as a check that the policies mean what the tests
  assert about their text.

## Rollout

One release. All three values default to inert, so the charts can ship ahead of any platform
enabling them, and a platform can enable `reservedHosts` before `tenantHostDelegation` to install
the veto before granting the capability. Nothing is deprecated.

## Open questions

1. **Level-1 tenants.** `spec.allowedHosts` on `Tenant/root` has no effect: the root namespace is
   rendered by `cozystack-basics`, not by the tenant chart, and carries no such annotation. So a
   level-1 tenant's own `spec.host` stays a trusted-caller field, and only its subtenants benefit.
   Making the root path emit the annotation would hand every level-1 tenant a subdomain of the
   platform apex the moment delegation is switched on, which is the hijack layer 4 exists to
   prevent — so the current no-op is the safe behaviour, and level-1 BYOD probably wants an
   explicitly-named platform value rather than a blanket annotation. Is deferring that acceptable?
2. **Claiming the parent's exact apex.** With delegation on, the seeded entry is the tenant's own
   apex and membership is apex-inclusive, so a child may claim its parent's exact apex. Only the
   parent can create that child, so it is not a trust-boundary crossing, but it yields two
   namespaces with one host label and a `HostnameConflict` with no defined winner. Should the
   delegation-seeded entry be strict-subdomain while explicit grants stay apex-inclusive?
3. **Reserved grants under GitOps.** Only `system:masters` may place a reserved zone in an
   allowlist, and Flux applies as `cozy-fluxcd` — so such a grant cannot be declared in GitOps and
   would be re-denied on every reconcile. Accept the limitation, or admit the `cozy-*` accounts
   there and rely on layer 4 alone for the veto?
4. **Scoping a grant.** A grant is inherited by the entire subtree, with no way to scope it to one
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

**Seed `reservedHosts` implicitly from `_cluster.root-host`,** so the default configuration protects
the platform apex without the operator enumerating `dashboard.`, `keycloak.` and friends. Rejected
after evaluation: every derived tenant apex is a subdomain of the platform apex, so reserving it
denies each tenant the very apex just delegated to it — it breaks the feature rather than hardening
it.

**A single trust set for both fields.** Rejected: it collapses the distinction that makes the
reserved list meaningful, and it would let a domain-granting component set hostnames directly.
