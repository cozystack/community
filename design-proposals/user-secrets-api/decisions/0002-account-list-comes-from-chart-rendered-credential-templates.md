# 0002. The chart renders a CredentialTemplate per account, and an API request writes at most one object

- **Number:** `0002`
- **Date:** `2026-11-19`
- **Status:** Accepted
- **Deciders:** `@kvaps`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4921`](https://github.com/cozystack/cozystack/pull/4921#pullrequestreview-5978089641)
- **Implemented in:** not yet

## Context

§1 of the proposal had the Cozystack API keep account Secrets in step with an application: on create it wrote one Secret per declared account before the HelmRelease and set their owner after it, on update it wrote the added ones before the release write and deleted the removed ones after it. §7 put the knowledge of which accounts exist into the ApplicationDefinition, as a list of account sources (`ValuesMap`, `Static`, and the declared but unbuilt `ValuesList`, `Expression`, `Webhook`), and the stored form into a `format` chosen from functions compiled into the API.

Implementing that in cozystack/cozystack#4921 turned one application write into a non-atomic sequence over several objects inside an aggregated API server. Reviews of #4921 on 2026-10-27 and 2026-10-29 by @myasnikovdaniil and on 2026-11-17 and 2026-11-18 by @lexfrei reproduced interleavings of two concurrent writes that delete an account Secret a live release references. The fix that followed, a per-application `coordination.k8s.io` Lease in `cozy-system`, was found to proceed without the lock when the Lease cannot be taken, to write a real Lease on a server-side dry run, and to read its baseline through a cached client. `mint` and `revoke` did not take the lock, so a mint recreating a missing Secret could still interleave with a prune. Each round added code to the request path and found another window.

Two engines also showed that the stored form is a property of the chart, not of the API. ClickHouse's operator watches no Secrets, so the proposal routed its verifier through a `valuesPath` and per-account `valuesFrom` entries that the API and the ApplicationDefinition reconciler had to compute identically. An engine that wants every user in one file, or a hash the API does not have, needed either a new API release or the unbuilt `Webhook` format.

None of the block had been released.

## Decision

An aggregated API request writes at most one backend object. The list of accounts and the stored form of a password come from the chart: it renders one `CredentialTemplate` (`cozystack.io/v1alpha1`, namespaced) per account, naming the username, the labels and the data of the account Secret, with the data written as templates over the password. A controller in cozystack-controller creates the Secret `<template>.account` for a new template, with the rendered value of a password it discards and origin `NotIssued`, and owned by the template, so removing a user removes its template through Helm and its Secret through garbage collection. `mint` and `revoke` render the template with a new password and update that one Secret under its `resourceVersion`. Application create, update and delete write only the HelmRelease. The ApplicationDefinition `spec.credentials` block, the account sources, `format` and `valuesPath` are dropped. The design is in [`../credential-templates.md`](../credential-templates.md).

## Why not the alternatives

- **Keep seeding in the API and fix the Lease lock** (the shape in #4921 at `8bdb9338`). Three rounds found a new window each time, and the lock is cross-replica coordination for a multi-object write that the request path should not make at all ([review](https://github.com/cozystack/cozystack/pull/4921#pullrequestreview-5978089641)).
- **Move seeding into a controller and keep the account sources in the ApplicationDefinition.** It removes the races as well, at a smaller change now. It lost on API surface: the sources are Go resolvers that grow with every new shape of account list (a list of objects, CEL, a webhook), each one an API release and a field that cannot be removed once shipped, while the chart already knows its accounts and can say so with `range`. Choosing it now and templates later would mean deprecating released ApplicationDefinition fields. This is a judgement on long-term surface, not a hard fact.
- **Render templates inside `mint` into one aggregated Secret for all users.** It needs every account at once, so a mint of one account becomes a multi-object read-and-write again. A file that aggregates users is built where it is consumed, from one Secret per account (a projected volume, or ClickHouse's second `users_xml` storage).
- **Put `CredentialTemplate` in `core.cozystack.io`.** That group-version is served whole by the aggregated Cozystack API through an APIService, so a stored CRD cannot live there. `cozystack.io` already carries ApplicationDefinition.

## Consequences

- One new CRD, `CredentialTemplate`, instead of the `spec.credentials` block. It is chart-owned: no tenant role grants it, and the API and the controller write only a Secret named `<template>.account` in the template's namespace and only one the template owns, so a template cannot overwrite a Secret it did not create.
- A new engine or hash needs a chart change, not an API release. The format functions move from a closed list to template functions next to sprig.
- An account exists once Flux has rendered the chart and the controller has seeded it, seconds after the HelmRelease. A mint before that answers a retryable conflict. The database does not exist before the render either.
- Changing the data of a template invalidates the stored value, since the password is gone. The controller resets such a Secret to `NotIssued` and records an Event, so chart authors keep non-secret settings out of the template (ClickHouse profiles and networks go to a chart-rendered file that ClickHouse merges with the per-user one).
- The conversion's seed step writes account Secrets before the templates exist. The controller adopts an unowned account Secret with the same name and username instead of resetting it, so migrated passwords keep working.
- The race tests, the lock, the `leases` grant, the seeding in the application registry, the account resolvers and the `valuesFrom` computation are removed from #4921. `mint`, `revoke`, the `Credential` view, the formats, the audit documentation and the tenant RBAC stay.
- `createRole` and `users.app` in the Postgres conversion are not affected by this record and are decided separately.

## Revisit if

A kind appears whose accounts cannot be rendered by its chart, for example keys whose existence only an external driver knows, or the chart-rendered list proves to lag too far behind the application for minting on create.
