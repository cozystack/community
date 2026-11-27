# 0002. Installation defaults come from one ConfigMap rendered from the platform value

- **Number:** `0002`
- **Date:** `2026-11-26`
- **Status:** Accepted
- **Deciders:** `@myasnikovdaniil`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#5107`](https://github.com/cozystack/cozystack/pull/5107#discussion_r4538651532)
- **Implemented in:** not yet ([`cozystack/cozystack#5107`](https://github.com/cozystack/cozystack/pull/5107), open)

## Context

The proposal has the platform chart render the `applicationDefaults` value into one ConfigMap, `cozy-system/cozystack-application-defaults`, and calls that ConfigMap an internal rendering detail of the value ([Configuration](../README.md#configuration)). The implementation reads it through a cache limited to that one object. Each create parses the whole map before it picks the entry for its kind, so an entry that cannot be parsed, or a reserved top-level field in any entry, turns defaults off for every kind until it is fixed. The create still goes through and returns a warning.

In review, `@lllamnyp` suggested selecting ConfigMaps by a label instead, so that an administrator could keep the defaults of each application kind in a ConfigMap of its own, with a small reconciler holding the complete set in memory ([comment](https://github.com/cozystack/cozystack/pull/5107#discussion_r4538651532)).

## Decision

Installation defaults keep coming from the one ConfigMap that the platform chart renders from `applicationDefaults`, and the API reads only that object. The platform value stays the only supported way to configure them.

## Why not the alternatives

- **ConfigMaps selected by a label, one per application kind** ([comment](https://github.com/cozystack/cozystack/pull/5107#discussion_r4538651532)). It gives administrators a second place to write defaults, outside the platform package, and the API would need a rule for two ConfigMaps that configure the same kind. What it would add, a broken entry turning off defaults for its own kind only, does not need it: parsing each entry on its own gives the same isolation inside one ConfigMap. This lost on a judgement call: neither the review nor the implementation turned up a configuration that one platform value cannot express.

## Consequences

Defaults are edited in one place, through the platform package, and reach the API through one watched object. A typo in one entry still turns defaults off for every kind until it is fixed, with a warning on each create and the reason in the API log. Parsing each entry on its own is the follow-up that would confine such a typo to its kind.

## Revisit if

Defaults have to be written by someone who cannot edit the platform package, for example a team that owns a single application kind.

---

<!--
Format follows Michael Nygard's architecture decision records
(https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)
and MADR (https://adr.github.io/madr/).

Once merged, the header block above is maintained (Status, Superseded by,
Implemented in must track reality) and the prose below it is frozen.
-->
