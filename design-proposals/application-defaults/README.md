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
- Reject unreadable or malformed configuration before writing a HelmRelease.

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

### Validation of configured defaults

The shared application storage in `cozystack-api` validates defaults using the target ApplicationDefinition's `spec.application.openAPISchema`. This is implemented once in the API server, with Kubernetes/OpenAPI validation libraries and a check for unknown field paths. Application authors provide their schema and do not write another validator for this mechanism.

Validation runs on every create request, after reading the ConfigMap and selecting the application's entry, before merging it into the user spec, checking quotas, running validating admission or writing a HelmRelease. Creation through apply and server-side dry-run use this same path. Existing instance reads and updates do not consult or validate installation defaults.

Defaults are a partial spec. Supplied field names, types and value constraints are checked, while object properties that the user may supply later can remain absent. Replacement values such as arrays are checked as whole values.

Unknown field paths are rejected, with the full configuration path in the error. Additional keys are accepted only where the schema declares a dictionary or free-form content through `additionalProperties` or `x-kubernetes-preserve-unknown-fields`. This is a stricter rule for installation configuration than ordinary JSON Schema validation, which permits extra properties when `additionalProperties` is omitted. It does not modify the ApplicationDefinition.

If a configured application's schema is missing or cannot be read, creation fails before writing a HelmRelease. Application-specific checks apply to the selected entry; the generic JSON and mapping-structure checks apply to the complete ConfigMap. An entry for a catalog not installed yet is checked against its schema once that application becomes available.

Saving the ConfigMap does not run these checks: Kubernetes sees its JSON payload as a string. Operators can exercise the creation path with `dry-run=server` before using the defaults for real instances. A separate admission webhook for ConfigMap updates is outside this proposal.

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

The API reads the ConfigMap directly from Kubernetes for each create request. It uses an uncached reader so a creation does not depend on a stale informer snapshot.

The create path supplements the spec before quota checks and validating admission, then writes it to `HelmRelease.spec.values`. Admission sees the values being saved. A rejected request writes no HelmRelease. Dry-run follows the same defaulting and validation path without persisting a resource.

Creation through apply must use the same create path. Updating an existing application does not apply installation defaults, even when a field is removed from the update.

Get, List, Watch and Helm rendering do not read this ConfigMap. Developer chart/schema defaulting keeps its existing behavior.

### Dashboard

The dashboard gets the same named ConfigMap through the Kubernetes API before showing a creation form. A namespaced Role grants only `get` on this object. It grants no list, watch or write access, and no access to other ConfigMaps in `cozy-system`.

Initial values follow the same priority order. The form uses a copy of the schema, leaving the ApplicationDefinition unchanged. RJSF must preserve explicit empty values and keep developer defaults working for rows and dictionary entries added after initialization.

The form and YAML editor share the prepared spec. Displayed defaults are submitted as fields in that spec, so a later configuration change does not silently replace those displayed values. A configuration refresh preserves user edits. Editing an existing application starts from its saved values and does not load installation defaults.

### Client behavior and stored values

Values sent by Terraform or another client count as user values, including defaults the client populated itself. They take precedence over installation defaults. A client must leave a field absent for installation defaulting to supply it.

Persisting installation defaults pins an application to those values. Changing a default is not a request to update its existing instances. The saved spec shows the effective values but does not record their source or the ConfigMap revision.

### Example: tenant Talos workers

[Platform defaults for tenant Talos workers](https://github.com/cozystack/cozystack/issues/3950) is an application of this mechanism. An installation can configure a factory URL, installer repository and registry mirrors under the KubernetesNodes plural, through the same mapping used for the database example above. The [KubernetesNodes split](https://github.com/cozystack/community/tree/main/design-proposals/kubernetes-nodes-split) keeps these settings with the worker pool application.

The factory URL selects the worker OS disk image. The KubernetesNodes chart includes that disk template in the KubevirtMachineTemplate name hash, so applying a new fallback during rendering can replace existing workers. Storing the chosen URL at creation keeps existing pools on their saved value.

Registry mirrors configure container image pulls inside the worker and are separate from the disk image source. Both settings use ordinary application fields; the shared defaulting code has no Talos-specific path.

## User-facing changes

Operators get the platform `applicationDefaults` setting. Application manifests keep the same API shape. New instances created with an omitted field may have that field filled in their stored spec.

Dashboard users see the prepared values before submitting a new instance. Operators change an existing instance by editing its spec.

## Upgrade and rollback compatibility

No migration rewrites existing HelmRelease values. Adding or changing installation defaults does not change existing KubernetesNodes disk URLs or KubevirtMachineTemplate names.

Deleting the ConfigMap stops installation defaulting for future creates. It does not remove values already stored in application specs. Rolling back the server leaves those values in place, as ordinary application settings already supported by their charts.

This proposal does not freeze developer chart defaults. Their existing read and rendering behavior, including changes during chart upgrades, is separate from installation defaulting.

## Security

Operators control the defaulting configuration. Application users may override its values, so this is a convenience mechanism, not an enforcement policy.

Authenticated dashboard readers can read the named ConfigMap. Its contents must not include credentials or other secrets. Existing secret reference fields remain the way to supply those where an application supports them.

## Failure and edge cases

- Missing ConfigMap: use no installation defaults.
- Forbidden or failed ConfigMap read: reject creation before writing a HelmRelease; the dashboard blocks creation and shows the error.
- Invalid JSON, a non-object root or a non-object application entry: reject creation. Validate the complete configuration, including entries for other applications.
- Reserved top-level fields: reject configuration.
- An unknown field or an invalid configured value for the selected application: reject creation with its configuration path, before any HelmRelease write.
- Missing or unreadable schema for an application with configured defaults: reject creation.
- Unknown plural: retain the entry for a future catalog; it matches no current application.
- Invalid user values or quota/admission rejection: preserve the existing rejection behavior, after considering installation defaults.
- Configuration changes during an open dashboard form: retain the displayed values and user edits.
- Required nulls that existing normalization treats as unset: preserve that repair behavior, with ordering to be settled during review.

## Testing

Go tests must cover merge precedence, nested objects, explicit empty values, configuration failures, quota and admission ordering, dry-run and creation through apply. Schema validation tests must reject unknown paths and wrong value types, allow partial objects and declared free-form dictionaries, and reject configured defaults when the target schema cannot be read. Lifecycle tests must verify that Get, List, Watch and Update do not use new installation defaults.

Helm tests cover the separate JSON ConfigMap, empty configuration, exclusion from shared chart values and the named-object RBAC grant.

Dashboard tests cover initial values, form/YAML transitions, edits during configuration refresh, explicit empty objects and arrays, and developer defaults in dynamically added entries. Edit mode must not receive installation defaults.

KubernetesNodes render tests cover factory URLs, installer repositories and mirrors from stored values. Both a pool with saved installation defaults and a pool created before the ConfigMap exists must keep their disk URL and template name after configuration changes and a later edit.

An unrelated application and an arbitrary application plural verify that the mechanism has no Kubernetes-specific path.

## Rollout

Ship the platform ConfigMap, API creation logic and dashboard support together. Install the named-object RBAC grant before the new dashboard needs to read it. Keep the configuration empty by default.

Operators can then configure defaults for new instances. Documentation must state the create-only behavior, client default precedence and ConfigMap visibility. There is no automatic conversion of old settings or update of existing instances.

## Open questions

- Should required-null normalization happen before installation defaulting, so a repaired user null receives the installation value? The prototype currently applies installation defaults before the existing normalization step.
- Should malformed configuration for an unrelated application block every create request, as proposed here, or only requests for that application?
- Is a GET refresh on dashboard navigation or focus sufficient for configuration changes, while preserving the values already shown to the user?

## Alternatives considered

### Application chart fallbacks

Passing settings through `_cluster` and consuming them in chart templates applies them at rendering time. It can change existing workloads, and nonempty developer chart values can mask the intended installation precedence. It also requires application-specific consumers.

### ApplicationDefinition schema defaults

The API applies schema defaults on reads. Changing them reaches existing instances and may be saved by a later client update. That does not provide the creation boundary needed here.

### Client-side defaults

Clients can already send the chosen values, and keep that ability. Implementing the policy in each dashboard, Terraform provider and command-line workflow gives no shared behavior for clients that omit fields.

### A new defaults API resource

A dedicated resource could provide typed validation and a more detailed lifecycle. This proposal needs a mapping in platform configuration and a snapshot during creation, so it uses a ConfigMap and the existing application spec.
