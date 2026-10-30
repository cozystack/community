# Installation defaults for applications

- Title: Installation defaults for applications
- Author(s): @myasnikovdaniil

## Overview

Installation defaults provide a shared configuration layer for every application registered through an ApplicationDefinition. The API applies these values when an application is created and stores the result in its HelmRelease. The order is user values, installation defaults, then developer chart/schema defaults.

Changing installation defaults affects future applications. It does not add installation defaults to existing applications on reads, updates or Helm rendering. This changes the behavior of application creation, even though it adds no fields or resources to the Cozystack API. Once an entry exists for an application kind, every client that omits a field of that kind takes the installation value instead of the chart default. That includes kubectl, Terraform, CI jobs and GitOps repositories, and it applies to the whole cluster. Values a client sets keep precedence.

## Scope

The mechanism belongs in the shared application creation path. It applies to applications from built-in and external catalogs without a list of supported kinds or application-specific consumers.

This proposal covers instance creation. Defaults for platform components, application retirement, registry configuration migration and changes to an application's own values schema are outside its scope.

## Decisions

No decision records yet. This is a new proposal.

## Context

Developer defaults come from chart values and application schemas. The aggregated API also exposes schema defaults when reading an application, so a schema default is not a default restricted to new instances.

Installation operators need to choose defaults without maintaining a custom version of every application chart. Today clients can repeat the settings in each application spec, but there is no common creation policy for all clients.

Kubernetes has the same shape in other places. The DefaultStorageClass admission plugin and LimitRanger write defaults into an object when it is created, and the OpenShift project template is applied only at creation.

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

The platform chart renders a separate ConfigMap named `cozystack-application-defaults` in `cozy-system`. Its `data["defaults.json"]` contains a JSON object with this mapping. The ConfigMap is an internal rendering detail, and the platform value `applicationDefaults` is the supported interface.

These values do not enter `cozystack-values` or HelmRelease `valuesFrom`. HelmReleases read `cozystack-values` through `valuesFrom`, so a change there reconciles all of them and the values reach every chart, while these defaults are meant for new instances only.

The configuration identifies applications by plural. It does not inspect kinds or maintain a list of supported applications. Entries for catalogs not installed yet can remain in the configuration.

### Configuration errors and validation

There is no admission-time validation of the ConfigMap payload against a catalog of applications. The payload is a JSON string, and external catalogs may be installed after the configuration is saved. Application names and schemas are discovered through ApplicationDefinitions at runtime.

On an application create request, the API reads the configuration and selects the entry by the target application's plural. The shared Go path can check that partial spec against the target ApplicationDefinition's `spec.application.openAPISchema`, without application-specific validators or prior knowledge of external catalogs. Supplied names, types and constraints are checked; required object properties that a user can supply later may be absent, while arrays are checked as complete replacement values. Unknown fields require a declared dictionary or free-form schema. Validation rules written in CEL (`x-kubernetes-validations`) are not evaluated for the entry.

These checks protect the merge. They do not make optional defaults a prerequisite for creation. If the ConfigMap cannot be read, its JSON or structure is invalid, the selected entry cannot be checked against its schema, or its values fail those checks, the API skips installation defaults and continues with the submitted spec. No part of a rejected entry is merged. The API logs the configuration problem and returns a warning to the client, on dry-run as well. Ordinary user-spec validation, quotas and admission still run and can reject the application.

Generic parsing and reserved-field checks apply to the complete configuration; schema checks concern only the selected application's entry. A malformed mapping can therefore prevent defaults from being used for that request, but cannot by itself prevent application creation. An unknown plural remains available for a catalog installed later, so a mistyped key has no effect and produces no message. To see which plurals a cluster serves, run `kubectl get applicationdefinitions -o custom-columns=NAME:.metadata.name,PLURAL:.spec.application.plural` and compare them with the keys in `applicationDefaults`. Plurals are not always regular, for example `postgreses` and `kuberneteses`.

The same check applies after an application upgrade. If the new schema no longer accepts a configured value, defaults for that application stop applying and each create returns the warning above.

Operators can test the result with `kubectl create --dry-run=server -o yaml` and a name that is not in use, because a dry-run create for an existing name returns AlreadyExists. A ConfigMap validating webhook is outside this design. A failed defaults lookup or check follows the same fallback during dry-run and actual creation.

### Merge rules

The root spec is a collection of user fields. An omitted spec or `spec: {}` permits installation defaults.

For each field missing from the user spec, copy the installation value. If both values are objects with declared properties and the user object is nonempty, recurse into it. An explicitly empty object inside spec stops installation defaulting for that object.

| User field | Installation field | Result |
| --- | --- | --- |
| omitted | `3` | `3` |
| `0` | `3` | `0` |
| `false` | `true` | `false` |
| `""` | `"example"` | `""` |
| `[]` | `["example"]` | `[]` |
| `{}` | `{a: 1}` | `{}` |
| `{a: 2}` (declared properties) | `{a: 1, b: 3}` | `{a: 2, b: 3}` |
| `{a: 2}` (dictionary) | `{a: 1, b: 3}` | `{a: 2}` |
| permitted `null` | any value | `null` |

Arrays are replaced as whole values, and so are objects without declared properties: typed dictionaries (`additionalProperties`) and free-form objects (`x-kubernetes-preserve-unknown-fields`). Installation defaults do not merge into their elements or entries. For a map of registry mirrors, a pool that declares its own entries receives none of the platform entries. Existing normalization of required, non-nullable null fields remains in place; its ordering is an open question below.

Top-level fields beginning with `_` remain reserved by the existing application API. Nested application data is not subject to that reservation.

### API creation

The ConfigMap is consulted only for application creation, including creation previews through server-side dry-run. Get, List, Watch, Update and Helm rendering do not read it. The create path reads the ConfigMap from a cache that the API server fills with a watch on that single object, and the API starts serving only after the cache has synced. A transient Kubernetes API error therefore does not reach the create path, and a saved change becomes visible to creates shortly after.

The create path merges usable defaults before quota checks and validating admission, then writes the resulting spec to `HelmRelease.spec.values`. Admission sees the values being saved. Dry-run follows that same path without persisting a resource. A quota or admission rejection still writes no HelmRelease, including when a configured default contributed to that rejection.

A failed defaults read is an optional-configuration failure: creation continues without installation defaults. The underlying Kubernetes API may still fail the later HelmRelease write, independently of this lookup. There is no additional availability requirement that the defaults ConfigMap must be readable for an application to be created.

Creation through apply uses the same create path. Updating an existing application does not apply installation defaults, even when a field is removed from the update. Developer chart/schema defaulting keeps its existing behavior.

### Dashboard

The merge is implemented only in Go. The dashboard does not read the ConfigMap, merge installation values, or calculate developer defaults locally. When the order page opens, it sends one creation request with `dryRun=All`, a placeholder name and an empty spec, and fills the form and the YAML editor from the spec the server returns. No additional ConfigMap RBAC grant is needed.

There are no further previews while the user edits, so a form costs the API one dry-run create. The request runs the same path as a real create: defaulting, conversion to a HelmRelease, validating admission and, for a Tenant, quota checks. On a test cluster it took about as long as a GET, roughly 150 to 200 ms that was mostly network latency, with occasional outliers around 0.6 s. The form opens as soon as the preview answers, an error included, and a failed preview does not block Deploy. The 8 seconds is a cap for a request that gets no answer at all: after that the preview counts as failed and the form opens without installation values. The real create validates the spec and applies installation defaults to any field the request omits, with user values winning.

The preview can fail. A webhook that declares side effects other than `None` or `NoneOnDryRun` is rejected on dry-run requests, so the preview fails for applications behind such a webhook. The form then opens without installation values and shows a notice, and Deploy still works. Components on the application create path must declare `sideEffects` accurately, and a component that declares dry-run support must honor it. Errors in the defaults configuration follow the server fallback and do not become dashboard failures.

The returned spec is used by both the form and YAML editor. The form renders a copy of the ApplicationDefinition schema with automatic default filling disabled, including nested objects, new array rows and dictionary entries, so RJSF does not add its own values over the server's. The ApplicationDefinition itself is unchanged. A field that is still empty after the preview keeps the dashboard's existing behavior, for example selecting the default option of a dynamic list such as storage classes. There is no TypeScript version of the merge algorithm and no hardcoded application defaults in the dashboard.

Displayed values are submitted as fields in the spec, so they are stored with the application, developer defaults included (see below). The dashboard does not display API warnings yet, so a skipped installation default shows up in the API response and the log but not in the form. Editing an existing application starts from its saved values and does not request a creation preview.

### Client behavior and stored values

Values sent by Terraform or another client count as user values, including defaults the client populated itself. They take precedence over installation defaults. A client must leave a field absent for installation defaulting to supply it. A client that serializes every field sends empty values, and those count too: Go clients built on the generated types, which declare fields such as `storageClass` and `size` without `omitempty`, do not receive installation defaults for those fields. The same applies to values displayed by a server preview and then submitted by the dashboard.

Developer defaults differ by client. The API shows them on reads, and a create request that omits a field leaves them unstored. The dashboard submits every value it displays, so an application created there stores developer defaults as well, while the same request sent with kubectl does not. This matches how the dashboard form works today, and it means the same intent can produce different stored specs depending on the client. Submitting only the fields the user changed is a possible later change to the dashboard.

Persisting installation defaults pins an application to those values. Changing a default is not a request to update existing instances. The saved spec does not record which fields came from defaults or which ConfigMap revision was used. Values alone cannot identify every instance created with a particular revision; investigating and repairing old choices requires inspecting saved specs and changing them deliberately. No provenance annotation or automatic repair of existing instances is introduced.

An operator applying the platform configuration does not apply or reconcile application instances.

Applying a manifest for an absent application goes through the create path, so installation defaults are stored. Applying it for an existing application goes through update, which does not read them, so stored defaults survive reapplication of the same manifest. The API does not store managed fields for applications, so no client owns the defaulted fields in the server-side apply sense. Apply merges the manifest onto the current object and keeps the fields the manifest does not mention, so removing a field from a manifest does not remove it from the application. This is how apply to the application API already behaves. The first update by any client, apply or patch, also saves the developer defaults the API returns on reads into the stored values.

Controllers that diff a manifest against the live object, such as Argo CD and Flux, see stored installation defaults as fields that exist only in the live object, the same way they already see developer defaults on reads. `kubectl apply` of an unchanged manifest sends nothing. Argo CD, Flux and Terraform have not been tested against these fields. The platform has no GitOps integration for applications today, and this proposal adds none.

### Example: tenant Talos workers

[Platform defaults for tenant Talos workers](https://github.com/cozystack/cozystack/issues/3950) is an application of this mechanism. An installation can configure a factory URL, installer repository and registry mirrors under the KubernetesNodes plural, through the same mapping used for the database example above. The [KubernetesNodes split](https://github.com/cozystack/community/tree/main/design-proposals/kubernetes-nodes-split) keeps these settings with the worker pool application.

The factory URL selects the worker OS disk image. The KubernetesNodes chart includes that disk template in the KubevirtMachineTemplate name hash, so applying a new fallback during rendering can replace existing workers. Storing the chosen URL at creation keeps existing pools on their saved value.

Registry mirrors configure container image pulls inside the worker and are separate from the disk image source. Both settings use ordinary application fields, and the shared defaulting code has no Talos-specific path. Mirrors configured here reach new pools only, and existing pools change theirs by editing their spec. The mirror map is a dictionary, so a pool that declares its own mirrors keeps exactly those and receives none of the platform entries.

## User-facing changes

Operators get the platform `applicationDefaults` setting. Application manifests keep the same API shape. New instances created with an omitted field have that field filled in their stored spec when the installation configures it.

Dashboard users see server-prepared values before submitting a new instance. In a manifest or the YAML editor, omitting a nested object lets installation defaults supply it, and writing that object as `{}` suppresses installation defaults inside it. Root `spec: {}` still permits defaulting of its fields. Operators change an existing instance by editing its spec.

A configured default is part of the effective request for quota and admission checks. If it exceeds a quota or violates a policy, the user receives the normal rejection and can supply a different value. A configuration lookup or validation failure instead skips defaults and continues creation.

## Upgrade and rollback compatibility

No migration rewrites existing HelmRelease values. Adding or changing installation defaults does not change existing KubernetesNodes disk URLs or KubevirtMachineTemplate names.

Deleting the ConfigMap stops installation defaulting for future creates. It does not remove values already stored in application specs. Rolling back the server leaves those values in place, as ordinary application settings already supported by their charts.

Developer chart and schema defaults keep their existing read and rendering behavior, including changes during chart upgrades. Applications created in the dashboard, or updated once by any client, carry them as saved values, as described under client behavior.

## Security

Operators control the defaulting configuration. Application users may override its values.

Installation defaults are not an enforcement mechanism. Clients can override them, and updates do not consult them. To require a value, use a ValidatingAdmissionPolicy or a webhook on the application resource. The create path runs admission on the merged spec, so the two work together.

Credentials and other secrets do not belong in this ConfigMap, just as they do not belong in platform package values. Applications use their existing secret reference fields where supported. This proposal adds no heuristic detection of secret-shaped fields or values: external applications can define arbitrary schemas.

The dashboard receives the prepared application spec through the application API. It needs no direct read access to the defaults ConfigMap, and this feature grants none. Values copied into an application are visible to readers of that application's spec.

## Failure and edge cases

- Missing, forbidden or failed ConfigMap read: continue without installation defaults.
- Invalid JSON, a non-object root, a non-object application entry or reserved top-level fields: skip defaults, report the configuration problem and continue creation.
- Unknown field, invalid configured value, or a missing or unreadable schema for the selected application: skip the selected defaults without merging a partial result; continue normal application validation and creation.
- Unknown plural, including a mistyped one: keep the entry for a future catalog. It matches no current application and produces no message.
- Application schema changed so that a configured value no longer passes the check: skip that application's defaults and return the warning on each create until the configuration is updated.
- A HelmRelease written directly, bypassing the application API, for example by the ComputePlane module or a platform migration: no installation defaults.
- Invalid user values or quota/admission rejection: preserve the existing rejection behavior. A default that fails quota or admission does not trigger a retry with different values.
- Creation preview fails: the form opens without installation values and shows a notice, Deploy still works, and the real create applies installation defaults to omitted fields.
- Configuration changes while a dashboard form is open: displayed values stay, and the create applies the current configuration to fields the submitted spec omits.
- Required nulls that existing normalization treats as unset: preserve that repair behavior, with ordering to be settled during review.

## Testing

Go tests cover merge precedence, nested objects, explicit empty values, quota and admission ordering, dry-run and creation through apply. Configuration failure tests must show that read, parsing, structure and schema-check failures leave the submitted spec untouched and still allow ordinary creation. Invalid configured values must never be saved as a partial merge. Separate tests preserve rejections for invalid user specs, quotas and admission.

Schema checks cover unknown paths, wrong types, partial objects and declared free-form dictionaries without application-specific code. A rule written in CEL that only the entry violates does not reject it. Dictionary values, such as registry mirrors, are replaced as a whole. Lifecycle tests verify that Get, List, Watch and Update do not consult installation defaults. The cached reader tests cover a missing ConfigMap and a change made while the API is running. Server-side apply tests cover actual create and update requests, reapplication after configuration changes and field ownership.

Helm tests cover the separate JSON ConfigMap, empty configuration and exclusion from shared chart values. The dashboard adds no ConfigMap read permissions.

Dashboard tests use the dry-run response as the source of displayed values. They cover the single preview when the page opens, new array and dictionary entries, form/YAML transitions, a failed preview with Deploy still available, the default option of dynamic lists for an empty field, and submission of the displayed spec. RJSF must not refill schema defaults independently. Edit mode must not issue creation previews.

KubernetesNodes render tests cover factory URLs, installer repositories and mirrors from stored values. Both a pool with saved installation defaults and a pool created before the ConfigMap exists must keep their disk URL and template name after configuration changes and a later edit.

An unrelated application and an arbitrary application plural verify that the mechanism has no Kubernetes-specific path.

## Rollout

Ship the platform ConfigMap, API creation logic and dashboard dry-run previews together. Keep the configuration empty by default. No additional dashboard ConfigMap RBAC is installed.

Documentation must state the create-only behavior, user-value precedence, fallback when defaults are unavailable or invalid, and the difference between omitted and empty objects. It must also explain that values sent by clients count as user choices, how to list the plurals a cluster serves, and how to check a configured entry with a dry-run create after the entry is applied and after application upgrades. There is no automatic conversion of old settings or update of existing instances.

## Open questions

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

### Application-specific defaulting in the API server

A path in the API server selected by the kind name would cover KubernetesNodes without a platform-wide mechanism. Every other application with its own needs would add another path to core, and external catalogs could not use it. The plural-keyed mapping covers any ApplicationDefinition with one shared path.

### MutatingAdmissionPolicy

MutatingAdmissionPolicy is a native create-time mutation, and a JSONPatch guarded by `has()` can fill only the missing fields. It would take the merge out of Go, but it does not fit this API server today. This comes from reading the API server code, not from a running prototype.

The API server keeps the policy plugin disabled. The plugin refuses every write until it has synced the policies, the server has no RBAC to list them, and clusters older than 1.36 do not serve the API, so using it sets a Kubernetes 1.36 floor for the management cluster. CEL has no recursion, so one policy cannot merge an arbitrary parameter object, and the platform chart would have to compile `applicationDefaults` into one policy per plural. The policy dispatcher needs a type converter for the matched kind from the cluster's OpenAPI document. When the kind is not published there yet it returns a 503 that bypasses `failurePolicy`, so creates of such an application would fail closed even with `failurePolicy: Ignore`. The application spec is raw JSON in Go, so only JSONPatch mutations are practical, and the round trip into the typed object would need its own tests.

The merge rules here, including atomic dictionaries and an explicit `{}` stopping defaulting, would have to be written as generated CEL conditions instead of Go code with unit tests.
