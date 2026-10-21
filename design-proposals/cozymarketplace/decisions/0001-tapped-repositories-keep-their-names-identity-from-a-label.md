# 0001. Tapped repositories keep their own names, and identity comes from a label

- **Number:** `0001`
- **Date:** `2026-08-31`
- **Status:** Accepted
- **Deciders:** `@IvanHunters`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#3940`](https://github.com/cozystack/cozystack/pull/3940)
- **Implemented in:** [`cozystack/cozystack#3940`](https://github.com/cozystack/cozystack/pull/3940), kind ownership in [`cozystack/cozystack#4424`](https://github.com/cozystack/cozystack/pull/4424)

## Context

The original design agreed a `community.<org>.<repo>` prefix on a tapped repository's cluster-scoped `PackageSource` name, so a community source could never shadow an official package name. Building it in `#3940` exposed a break: a repository's `ApplicationDefinition` carries a `chartRef.name`, and renaming the `PackageSource` to the prefixed form left that `chartRef.name` pointing at a name the prefixed artifact no longer had, so the definition no longer resolved to its chart.

Three ways out were put on the table in the pull request ([comment](https://github.com/cozystack/cozystack/pull/3940#issuecomment-5429455144)): have the materializer rewrite each `chartRef.name` to the prefixed artifact name; have `cozyrds` reference the artifact through a template resolved at install time; or stop renaming the `PackageSource` and replace the prefix with a label plus a refusal on name collision.

## Decision

A tapped repository keeps its own declared `PackageSource` names, with no `community.` prefix. Tap identity is carried by the `apps.cozystack.io/marketplace-tap` label, not by a name prefix. An exact `PackageSource` name that collides with an existing core component, or with another repository's source that is not this one's own materialization, is refused at connect time rather than overwritten, enforced both in `cozypkg tap` and in the operator's materializer (`collision.PackageSourceName`). This check is on the exact name only; it does not cover the other ways a tapped name can still collide, listed under Consequences. `ApplicationDefinition` kind ownership is handled separately: the oldest definition owns a kind, and a second definition claiming the same kind is refused by an admission webhook ([`#4424`](https://github.com/cozystack/cozystack/pull/4424)).

## Why not the alternatives

The three were argued in [the pull request comment](https://github.com/cozystack/cozystack/pull/3940#issuecomment-5429455144); the chosen one was called "a cleaner seam than a name prefix" in [the 2026-08-31 review](https://github.com/cozystack/cozystack/pull/3940#pullrequestreview-5070284148).

- **Materializer rewrites `chartRef.name` to the prefixed artifact name.** Keeps the prefix guarantee, but makes the operator edit every definition the repository ships on every materialization, adding a rewrite step and a class of drift between what the author wrote and what runs.
- **`cozyrds` references the artifact through a template resolved at install.** Defers the name to install time instead of fixing it at author time, adding indirection to a reference that was otherwise static.
- **Keep the declared name, identify by label, refuse on collision (chosen).** The least machinery: no rewrite, no deferred resolution. It gives up the prefix guarantee from the original design, so collision is handled by refusal instead of namespacing. It won on that trade, as a judgement call recorded in the review rather than a hard constraint.

## Consequences

- There is no automatic namespacing. A tapped repository cannot share an exact `PackageSource` name with a core component or another tapped source; the collision is refused at connect, and resolving it means renaming upstream.
- The exact-name check does not catch every collision. Two names collapse in the artifact layer because `naming.ArtifactName` turns `.` into `-`, so `cozystack.x` and `cozystack-x` map to the same `ExternalArtifact` name; and a component's release name can still clash with a platform `HelmRelease`. Neither is refused by the exact `PackageSource`-name check. These are known gaps, tracked as [`cozystack/cozystack#4977`](https://github.com/cozystack/cozystack/issues/4977) and [`cozystack/cozystack#4975`](https://github.com/cozystack/cozystack/issues/4975).
- Kind ownership is first-come. Because the oldest `ApplicationDefinition` owns a kind and a later one is refused, a tapped repository that registers a kind before the platform ships the same kind keeps it, and the platform's later definition is the one refused. Platform definitions do not automatically win. This inversion, and the related cases of a name the platform adds later and the dashed form of a reserved name, are the known residual gaps, tracked as the kind-ownership-by-origin item in the 1.7 marketplace epic ([`cozystack/cozystack#4981`](https://github.com/cozystack/cozystack/issues/4981)).
- The `community.` prefix agreed in the original design is gone from `PackageSource` names. The only prefix that remains is on the Flux source object the tap creates (`tap-<org>-<repo>`), which is not the `PackageSource` name.

## Revisit if

A policy lands that platform-owned definitions win a kind over an earlier tapped one, which would replace the oldest-wins rule and change what this decision guarantees about names the platform adds later.
