# 0002. The chart renders a CredentialTemplate per account, and an API request writes at most one object

- **Number:** `0002`
- **Date:** `2026-11-19`
- **Status:** Accepted
- **Deciders:** `@kvaps`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4921`](https://github.com/cozystack/cozystack/pull/4921#pullrequestreview-5978089641), [`cozystack/community#137`](https://github.com/cozystack/community/pull/137)
- **Implemented in:** not yet

## Context

As accepted ([README at `aee08fd`](https://github.com/cozystack/community/blob/aee08fd/design-proposals/user-secrets-api/README.md)), the proposal had the Cozystack API keep account Secrets in step with an application (§1): on create it wrote one Secret per declared account before the HelmRelease and set their owner after it, on update it wrote the added ones before the release write and deleted the removed ones after it, and a mint on a declared account with no Secret recreated it. §7 put the knowledge of which accounts exist into a `credentials` block of the ApplicationDefinition, as a list of account sources (`ValuesMap`, `Static`, and the declared but unbuilt `ValuesList`, `Expression` and `Webhook`), together with the `format`, the `secretLabels` and, for ClickHouse, a `valuesPath` that the API and the ApplicationDefinition reconciler both turned into `valuesFrom` entries (§3).

Implementing that in cozystack/cozystack#4921 turned one application write into a non-atomic sequence over several objects inside an aggregated API server. Reviews of #4921 on 2026-10-27 and 2026-10-29 by @myasnikovdaniil and on 2026-11-17 and 2026-11-18 by @lexfrei reproduced interleavings of two concurrent writes that delete an account Secret a live release references. The fix that followed, a per-application `coordination.k8s.io` Lease in `cozy-system`, was found to proceed without the lock when the Lease cannot be taken, to write a real Lease on a server-side dry run, and to read its baseline through a cached client, and `mint` did not take it at all. Each round added code to the request path and found another window.

None of the block had been released.

## Decision

A request to the aggregated API writes at most one backend object, the best-effort Event of a mint or a revoke aside. The chart of a kind renders one `CredentialTemplate` (`cozystack.io/v1alpha1`, namespaced) per account, which names the username, the `format` and the labels of the account Secret, and for ClickHouse a `valuesPath`. A controller in cozystack-controller seeds the Secret of a new template with a discarded password, adopts the unowned Secrets the conversion wrote, and mirrors the Secret's record and `resourceVersion` into the template's status; it seeds only releases marked for it, which every release the API creates is and a converted one becomes, so the accounts of an unconverted release keep their passwords. `Credential` reads only templates, and `mint` and `revoke` build the whole Secret from the template and write it under the mirrored version without reading it. Formats outside the built-in four are registered cluster-wide as `CredentialFormat` webhooks. Application create, update and delete write only the HelmRelease, and the ApplicationDefinition `credentials` block is dropped.

## Why not the alternatives

- **Keep seeding in the API and fix the Lease lock** (#4921 at `8bdb9338`). Three rounds found a new window each time, and the lock is cross-replica coordination for a multi-object write that the request path should not make ([review](https://github.com/cozystack/cozystack/pull/4921#pullrequestreview-5978089641)).
- **Move seeding into a controller and keep the account sources in the ApplicationDefinition.** It removes the races too, with a smaller change now. It lost on API surface: the sources are resolvers in the API that grow with every new shape of account list, each a release and a field that cannot be removed once shipped, while the chart already knows its accounts. Choosing it now and templates later would mean deprecating released fields. A judgement on long-term surface, not a hard fact.
- **Template the Secret's data in the CredentialTemplate**, with sprig and format functions over the password (the first revision of this record). It lets a chart emit any shape, ClickHouse's per-user XML included, but it runs chart-supplied templates over the plaintext inside the API and drops the abstract description of an account that the block had. An abstract `format` keeps the Secret's layout fixed, and a registered webhook covers the hashes the built-ins lack ([review](https://github.com/cozystack/community/pull/137)).
- **Point a template at a webhook directly.** The plaintext would go wherever a template says. Registering formats cluster-wide, writable only by platform administrators, keeps a template harmless whatever creates it.
- **Put `CredentialTemplate` in `core.cozystack.io`.** That group-version is served whole by the aggregated API through an APIService, so a CRD cannot live there.

## Consequences

- One new namespaced CRD, `CredentialTemplate`, and one cluster-scoped, `CredentialFormat`, instead of the `credentials` block. Templates are chart-owned and no tenant role grants either kind. The controller writes only a Secret named `<template>.account` in the template's namespace, and never one it did not create or adopt.
- An account exists once Flux has rendered the chart and the controller has seeded it, seconds after the HelmRelease. A mint before that creates the Secret itself, as one object. The view lags a mint until the controller mirrors the record.
- A chart that changes an account's format resets that account to `NotIssued`, because a stored value cannot be converted without the password.
- ClickHouse still needs a Helm run to apply a mint, through `valuesFrom`. A sidecar composing `users.d` from the account Secrets would remove that and is left for later.
- The credentials code in the API reads no Secret. cozystack-api keeps its Secret access for `tenantsecrets`, so this narrows the code that could leak a Secret, not the API's permissions.
- From #4921 go the lock, the `leases` grant, the seeding in the application registry, the account resolvers, the `valuesFrom` computation in the API and the ApplicationDefinition block. `mint`, `revoke`, `Credential`, the formats, the audit documentation and the tenant RBAC stay.
- `createRole` and `users.app` in the Postgres conversion are decided separately.

## Revisit if

A kind appears whose accounts its chart cannot render, for example keys whose existence only an external driver knows, or the chart-rendered list proves to lag too far behind the application for a mint right after create.
