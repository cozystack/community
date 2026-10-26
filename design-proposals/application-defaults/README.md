# Installation defaults for applications

- Title: Installation defaults for applications
- Author(s): @myasnikovdaniil

## Overview

Installation defaults provide a shared configuration layer for every application registered through an ApplicationDefinition. The API applies these values when an application is created and stores the result in its HelmRelease. The order is user values, installation defaults, then developer chart/schema defaults.

Changing installation defaults affects future applications. It does not add installation defaults to existing applications on reads, updates or Helm rendering. This changes the behavior of application creation, even though it adds no fields or resources to the Cozystack API.

## Scope

The mechanism belongs in the shared application creation path. It applies to applications from built-in and external catalogs without a list of supported kinds or application-specific consumers.

This proposal covers instance creation. Defaults for platform components, application retirement, registry configuration migration and changes to an application's own values schema are outside its scope.

## Decisions

No decision records yet. This is a new proposal.

## Context

Developer defaults come from chart values and application schemas. The aggregated API also exposes schema defaults when reading an application, so a schema default is not a default restricted to new instances.

Installation operators need to choose defaults without maintaining a custom version of every application chart. Today clients can repeat the settings in each application spec, but there is no common creation policy for all clients.

### The problem

An installation needs to choose values for new application instances without repeating those choices in every client and every manifest. A client that omits a field currently gets the developer default, even when the installation needs a different storage class, disk size or image source.

Defaults evaluated during later reads or renders can also reach existing instances that omitted the field. Operators need a boundary between settings for future creations and deliberate changes to instances already running.

The shared API creation path can supply that boundary. It discovers the application through its definition, supplements its spec and saves the chosen values with that instance.

## Goals

- Provide installation defaults for every ApplicationDefinition, including external catalogs.
- Give user-supplied values precedence over installation defaults.
- Persist installation defaults only when a new instance is created, including creation through apply.
- Leave existing instances unaffected by installation configuration changes.
- Show the values to be submitted in the dashboard before creation.
- Keep application creation available when installation defaults cannot be loaded or applied.
- Implement the merge once in Go and use server-side dry-run for dashboard previews.

### Non-goals

- Apply configuration changes to existing instances or roll out fleet-wide updates.
- Store developer chart/schema defaults in the API creation path.
- Add a Cozystack resource, a spec field or an application-specific fallback.
- Convert the old platform `registries` setting or remove its renderer.
- Introduce default profiles, tenant overrides or a separate secrets mechanism.

## Design

### Configuration

Add `applicationDefaults: {}` to platform values. Keys are the exact `ApplicationDefinition.spec.application.plural` values. Each entry is a partial spec for that application.

```yaml
applicationDefaults:
  postgreses:
    storageClass: replicated
    size: 20Gi
  kubernetesnodeses:
    talos:
      imageFactoryURL: https://factory.example.com
      installerRepository: registry.example.com/installer
      registryMirrors:
        ghcr.io:
          endpoints:
            - https://registry.example.com
```

The platform chart renders a separate ConfigMap named `cozystack-application-defaults` in `cozy-system`. Its `data["defaults.json"]` contains a JSON object with this mapping. These values do not enter `cozystack-values` or HelmRelease `valuesFrom`.

The configuration identifies applications by plural. It does not inspect kinds or maintain a list of supported applications. Entries for catalogs not installed yet can remain in the configuration.

### Configuration errors and validation

There is no admission-time validation of the ConfigMap payload against a catalog of applications. The payload is a JSON string, and external catalogs may be installed after the configuration is saved. Application names and schemas are discovered through ApplicationDefinitions at runtime.

On an application create request, the API reads the configuration and selects the entry by the target application's plural. The shared Go path can check that partial spec against the target ApplicationDefinition's `spec.application.openAPISchema`, without application-specific validators or prior knowledge of external catalogs. Supplied names, types and constraints are checked; required object properties that a user can supply later may be absent, while arrays are checked as complete replacement values. Unknown fields require a declared dictionary or free-form schema.

These checks protect the merge. They do not make optional defaults a prerequisite for creation. If the ConfigMap cannot be read, its JSON or structure is invalid, the selected entry cannot be checked against its schema, or its values fail those checks, the API skips installation defaults and continues with the submitted spec. No part of a rejected entry is merged. The API logs the configuration problem and may return a warning; ordinary user-spec validation, quotas and admission still run and can reject the application.

Generic parsing and reserved-field checks apply to the complete configuration; schema checks concern only the selected application's entry. A malformed mapping can therefore prevent defaults from being used for that request, but cannot by itself prevent application creation. An unknown plural remains available for a catalog installed later.

Operators can test the result with an application create using `dry-run=server`. A ConfigMap validating webhook is outside this design. A failed defaults lookup or check follows the same fallback during dry-run and actual creation.

### Merge rules

The root spec is a collection of user fields. An omitted spec or `spec: {}` permits installation defaults.

For each field missing from the user spec, copy the installation value. If both values are objects and the user object is nonempty, recurse into it. An explicitly empty object inside spec stops installation defaulting for that object.

| User field | Installation field | Result |
| --- | --- | --- |
| omitted | `3` | `3` |
| `0` | `3` | `0` |
| `false` | `true` | `false` |
| `""` | `"example"` | `""` |
| `[]` | `["example"]` | `[]` |
| `{}` | `{a: 1}` | `{}` |
| `{a: 2}` | `{a: 1, b: 3}` | `{a: 2, b: 3}` |
| permitted `null` | any value | `null` |

Arrays are replaced as whole values. Installation defaults do not merge into their elements. Existing normalization of required, non-nullable null fields remains in place; its ordering is an open question below.

Top-level fields beginning with `_` remain reserved by the existing application API. Nested application data is not subject to that reservation.

### API creation

The ConfigMap is consulted only for application creation, including creation previews through server-side dry-run. Get, List, Watch, Update and Helm rendering do not read it. The baseline uses a direct Kubernetes reader; caching and its freshness behavior remain an open question below.

The create path merges usable defaults before quota checks and validating admission, then writes the resulting spec to `HelmRelease.spec.values`. Admission sees the values being saved. Dry-run follows that same path without persisting a resource. A quota or admission rejection still writes no HelmRelease, including when a configured default contributed to that rejection.

A failed defaults read is an optional-configuration failure: creation continues without installation defaults. The underlying Kubernetes API may still fail the later HelmRelease write, independently of this lookup. There is no additional availability requirement that the defaults ConfigMap must be readable for an application to be created.

Creation through apply uses the same create path. Updating an existing application does not apply installation defaults, even when a field is removed from the update. Developer chart/schema defaulting keeps its existing behavior.

### Dashboard

The merge is implemented only in Go. The dashboard does not read the ConfigMap, merge installation values, or calculate developer defaults locally. It uses the existing application creation endpoint with `dryRun=All` to obtain the spec prepared by the server. No additional ConfigMap RBAC grant is needed.

The preview uses the real application name and tenant namespace, so application-specific name constraints and admission run through the normal API path. The name can be entered before initial settings are loaded. A preview error leaves the editor available for correction. Errors in the defaults configuration follow the server fallback and do not become dashboard configuration-loading failures.

The returned spec is used by both the form and YAML editor. The form renders a copy of the ApplicationDefinition schema with automatic default filling disabled, including nested objects, new array rows and dictionary entries. The ApplicationDefinition itself is unchanged. Subsequent previews also go to the server; there is no TypeScript version of the merge algorithm and no hardcoded application defaults in the dashboard.

Displayed values are submitted as fields in the spec. Newer user edits invalidate older preview responses, so a slow request cannot overwrite what the user typed. A refreshed preview preserves values already supplied in the request. Editing an existing application starts from its saved values and does not request a creation preview.

### Client behavior and stored values

Values sent by Terraform or another client count as user values, including defaults the client populated itself. They take precedence over installation defaults. A client must leave a field absent for installation defaulting to supply it. The same applies to values displayed by a server preview and then submitted by the dashboard.

Persisting installation defaults pins an application to those values. Changing a default is not a request to update existing instances. The saved spec does not record which fields came from defaults or which ConfigMap revision was used. Values alone cannot identify every instance created with a particular revision; investigating and repairing old choices requires inspecting saved specs and changing them deliberately. No provenance annotation or automatic repair of existing instances is introduced.

An operator applying the platform configuration does not apply or reconcile application instances. The separate question about server-side apply concerns clients creating an application with an apply PATCH. The storage path distinguishes an absent instance from an existing one: only the create branch adds installation defaults. Reapplying an existing application does not reload them. No separate defaults field manager is introduced; tests must exercise actual apply creation and later reapplication, including the aggregated API's existing field-ownership behavior.

### Example: tenant Talos workers

[Platform defaults for tenant Talos workers](https://github.com/cozystack/cozystack/issues/3950) is an application of this mechanism. An installation can configure a factory URL, installer repository and registry mirrors under the KubernetesNodes plural, through the same mapping used for the database example above. The [KubernetesNodes split](https://github.com/cozystack/community/tree/main/design-proposals/kubernetes-nodes-split) keeps these settings with the worker pool application.

The factory URL selects the worker OS disk image. The KubernetesNodes chart includes that disk template in the KubevirtMachineTemplate name hash, so applying a new fallback during rendering can replace existing workers. Storing the chosen URL at creation keeps existing pools on their saved value.

Registry mirrors configure container image pulls inside the worker and are separate from the disk image source. Both settings use ordinary application fields; the shared defaulting code has no Talos-specific path.

## User-facing changes

Operators get the platform `applicationDefaults` setting. Application manifests keep the same API shape. New instances created with an omitted field may have that field filled in their stored spec.

Dashboard users see server-prepared values before submitting a new instance. Omitting a nested object lets installation defaults supply it; writing that object as `{}` suppresses installation defaults inside it. Root `spec: {}` still permits defaulting of its fields. Operators change an existing instance by editing its spec.

A configured default is part of the effective request for quota and admission checks. If it exceeds a quota or violates a policy, the user receives the normal rejection and can supply a different value. A configuration lookup or validation failure instead skips defaults and continues creation.

## Upgrade and rollback compatibility

No migration rewrites existing HelmRelease values. Adding or changing installation defaults does not change existing KubernetesNodes disk URLs or KubevirtMachineTemplate names.

Deleting the ConfigMap stops installation defaulting for future creates. It does not remove values already stored in application specs. Rolling back the server leaves those values in place, as ordinary application settings already supported by their charts.

This proposal does not freeze developer chart defaults. Their existing read and rendering behavior, including changes during chart upgrades, is separate from installation defaulting.

## Security

Operators control the defaulting configuration. Application users may override its values.

Credentials and other secrets do not belong in this ConfigMap, just as they do not belong in platform package values. Applications use their existing secret reference fields where supported. This proposal adds no heuristic detection of secret-shaped fields or values: external applications can define arbitrary schemas.

The dashboard receives the prepared application spec through the application API. It needs no direct read access to the defaults ConfigMap, and this feature grants none. Values copied into an application are visible to readers of that application's spec.

## Failure and edge cases

- Missing, forbidden or failed ConfigMap read: continue without installation defaults.
- Invalid JSON, a non-object root, a non-object application entry or reserved top-level fields: skip defaults, report the configuration problem and continue creation.
- Unknown field, invalid configured value, or a missing or unreadable schema for the selected application: skip the selected defaults without merging a partial result; continue normal application validation and creation.
- Unknown plural: retain the entry for a future catalog; it matches no current application.
- Invalid user values or quota/admission rejection: preserve the existing rejection behavior. A default that fails quota or admission does not trigger a retry with different values.
- Configuration changes during an open dashboard form: retain displayed values and user edits. A later preview uses those values as user input.
- A preview response arriving after newer edits: discard it.
- Required nulls that existing normalization treats as unset: preserve that repair behavior, with ordering to be settled during review.

## Testing

Go tests cover merge precedence, nested objects, explicit empty values, quota and admission ordering, dry-run and creation through apply. Configuration failure tests must show that read, parsing, structure and schema-check failures leave the submitted spec untouched and still allow ordinary creation. Invalid configured values must never be saved as a partial merge. Separate tests preserve rejections for invalid user specs, quotas and admission.

Schema checks cover unknown paths, wrong types, partial objects and declared free-form dictionaries without application-specific code. Lifecycle tests verify that Get, List, Watch and Update do not consult installation defaults. Server-side apply tests cover actual create and update requests, reapplication after configuration changes and field ownership.

Helm tests cover the separate JSON ConfigMap, empty configuration and exclusion from shared chart values. The dashboard adds no ConfigMap read permissions.

Dashboard tests use dry-run responses as the source of displayed values. They cover initial loading, new array and dictionary entries, form/YAML transitions, user edits while a request is pending, failed previews, and submission of the displayed spec. RJSF must not refill schema defaults independently. Edit mode must not issue creation previews.

KubernetesNodes render tests cover factory URLs, installer repositories and mirrors from stored values. Both a pool with saved installation defaults and a pool created before the ConfigMap exists must keep their disk URL and template name after configuration changes and a later edit.

An unrelated application and an arbitrary application plural verify that the mechanism has no Kubernetes-specific path.

## Rollout

Ship the platform ConfigMap, API creation logic and dashboard dry-run previews together. Keep the configuration empty by default. No additional dashboard ConfigMap RBAC is installed.

Documentation must state the create-only behavior, user-value precedence, fallback when defaults are unavailable or invalid, and the difference between omitted and empty objects. It must also explain that values sent by clients count as user choices. There is no automatic conversion of old settings or update of existing instances.

## Open questions

- Should the create path use a cached ConfigMap reader? A cache could reduce Kubernetes reads during creation and preview, but its freshness and behavior after a failed refresh need an agreed contract. This does not affect Get/List/Watch/Update, which never consult the defaults configuration.
- Should required-null normalization happen before installation defaulting, so a repaired user null receives the installation value? The prototype currently applies installation defaults before the existing normalization step.

## Alternatives considered

### Application chart fallbacks

Passing settings through `_cluster` and consuming them in chart templates applies them at rendering time. It can change existing workloads, and nonempty developer chart values can mask the intended installation precedence. It also requires application-specific consumers.

### ApplicationDefinition schema defaults

The API applies schema defaults on reads. Changing them reaches existing instances and may be saved by a later client update. That does not provide the creation boundary needed here.

### Client-side defaults

Clients can already send the chosen values, and keep that ability. Implementing the policy in each dashboard, Terraform provider and command-line workflow gives no shared behavior for clients that omit fields.

### A dashboard merge implementation

Reading the ConfigMap in the dashboard and implementing the merge in TypeScript duplicates the Go rules and requires extra access to configuration. Shared fixtures would test agreement between two implementations but would still leave two implementations to maintain. A server-side dry-run provides the preview through the same path used for creation.

### A new defaults API resource

A dedicated resource could provide typed validation and a more detailed lifecycle. This proposal needs a mapping in platform configuration and a snapshot during creation, so it uses a ConfigMap and the existing application spec.
