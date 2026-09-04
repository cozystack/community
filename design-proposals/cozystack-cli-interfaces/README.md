# Cozystack user command-line interface

- **Title:** `Cozystack command-line interface for tenants and managed-service users`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-08-05`
- **Status:** Review

## Overview

Cozystack exposes tenant applications through its aggregated Kubernetes API, `ApplicationDefinition` metadata, and dynamically published OpenAPI schemas. The dashboard consumes that surface, but terminal users still need to understand raw resource names and implementation details to discover, create, inspect, and wait for managed services.

This proposal introduces one user-facing CLI, `cozyctl`, for tenants and managed-service users. It discovers the catalog and application schemas from the connected cluster, uses normal kubeconfig authentication and Kubernetes RBAC, and provides a stable application-oriented command tree without compiling every application type into the binary.

An earlier revision proposed a second binary, `cozystackctl`, for platform operators and attempted to combine package management, readiness, diagnostics, repository management, and tenant administration in one client-side tool. Product and architecture review changed that direction: platform lifecycle and upgrade orchestration belong to a durable in-cluster API and controller, with any operator CLI acting only as one client of that API. That work is moved to a separate `platform-lifecycle-operator` proposal; this document is intentionally narrowed to `cozyctl`.

## Scope and related proposals

This proposal defines the user-facing command organization, runtime discovery, configuration, output contract, and security boundary of `cozyctl`.

Platform installation, upgrades, health gates, and unattended operation are separate lifecycle-controller work. Unified health reporting is proposed in [community#64](https://github.com/cozystack/community/pull/64), and future declarative platform and tenant configuration is proposed in [community#16](https://github.com/cozystack/community/pull/16).

## Context

The aggregated Cozystack API registers application resources dynamically under `apps.cozystack.io`. `ApplicationDefinition` publishes kind names, descriptions, categories, tags, OpenAPI schema, and selectors for related resources. Installing a package can therefore add a managed application type without requiring a new client release.

Raw `kubectl` remains useful as an escape hatch, but it exposes Kubernetes and Helm implementation nouns rather than the service catalog model presented by Cozystack. A user should be able to discover available services, validate a manifest, create an instance, wait for readiness, and find its tenant-visible endpoints without knowing which HelmRelease, Service, or Secret implements it.

### The problem

There is no maintained terminal interface for the same application-oriented workflows available through the dashboard. Static generated clients would lag behind the catalog, while making every discovered application a root command would make help, completion, and scripts differ from cluster to cluster.

## Goals

- Provide one documented CLI for tenants and managed-service users.
- Discover available application kinds and their schemas from the connected cluster at runtime.
- Provide generic catalog, CRUD, validation, waiting, and related-resource inspection workflows.
- Keep scripts stable through machine-readable output, predictable exit codes, and explicit tenant selection.
- Reuse kubeconfig authentication and Kubernetes RBAC without introducing another credential store.
- Keep the server authoritative for validation, defaulting, admission, and authorization.

### Non-goals

- Platform installation, upgrades, package or Tap management, cluster health, diagnostics collection, or automated remediation.
- Platform-admin tenant lifecycle operations.
- Replacing `kubectl` for arbitrary Kubernetes resources.
- Inferring service-specific actions such as database shells, VM consoles, or kubeconfig retrieval from JSON schema alone.
- Defining a plugin system before a real external consumer exists.
- Preserving the command structure of an older unreleased `cozyctl` prototype.

## Design

### Command tree

```text
cozyctl
├── context
│   ├── list
│   ├── use
│   └── current
├── tenant
│   ├── list
│   └── use
├── catalog
│   ├── list
│   └── describe <type>
├── app
│   ├── list [type]
│   ├── get <type> <name>
│   ├── create <type> <name>
│   ├── update <type> <name>
│   ├── delete <type> <name>
│   ├── wait <type> <name>
│   └── resources <type> <name>
├── version
└── completion
```

Application types remain arguments below stable commands. This prevents collisions with built-ins and keeps documentation and scripts stable across clusters with different catalogs.

### Discovery and validation

The CLI combines Kubernetes API discovery, `ApplicationDefinition`, and the server-published OpenAPI schema. Generic commands use a dynamic client and unstructured objects, so a newly installed application type appears without rebuilding `cozyctl`.

Discovery supplies kind aliases, supported verbs, descriptions, categories, schema validation, and selectors for related resources. Client-side validation improves feedback, but the API server remains authoritative and its rejection is returned unchanged in structured form.

### Context and tenant selection

`cozyctl` uses standard client-go kubeconfig loading, including `--kubeconfig`, `KUBECONFIG`, and `--context`. Authentication remains in kubeconfig and supported authentication plugins.

The CLI may store non-secret preferences such as the selected context, tenant, output format, and color mode under the platform-appropriate XDG directory. It references kubeconfig entries and never copies tokens, client certificates, or private keys.

`cozyctl tenant use` changes only the local default. Every tenant-scoped command accepts an explicit `--tenant` for automation, and access is verified through normal API calls rather than inferred from local configuration.

### Output and automation contract

- Human-readable tables are the default on an interactive terminal.
- `--output=json` and `--output=yaml` write only machine-readable data to stdout; progress, warnings, and errors go to stderr.
- Table columns may grow, so scripts must consume structured output.
- Watch output uses newline-delimited JSON when JSON output is requested.
- Partial discovery or list failures are explicit, mark the result incomplete, and return a non-zero exit code.
- Mutating commands support consistent confirmation, dry-run, waiting, and timeout flags where the API supports them.
- Secrets and credential values never appear in diagnostic or structured output unless a dedicated user workflow explicitly retrieves an already-authorized tenant-visible Secret.

### Related resources

`cozyctl app resources` follows selectors declared by `ApplicationDefinition` and returns only resources visible through the caller's tenant-facing API and RBAC. It does not search implementation namespaces or bypass Secret filtering. If metadata cannot identify a relationship unambiguously, the CLI reports that limitation rather than guessing.

## User-facing changes

Typical workflows become:

```console
cozyctl catalog list
cozyctl catalog describe postgresql
cozyctl app create postgresql production -f postgres.yaml
cozyctl app wait postgresql production
cozyctl app resources postgresql production
```

Installing an additional application package makes its types visible through catalog discovery without a `cozyctl` release. Users who need raw Kubernetes inspection continue to use `kubectl`.

## Upgrade and rollback compatibility

The CLI adds no persisted cluster state beyond application resources the user explicitly creates or changes. Rolling back the binary restores the older client; Kubernetes discovery and the API server remain the compatibility boundary.

A newer client connected to an older cluster exposes only discovered operations and reports missing required API groups clearly. An older client continues to operate on known resource kinds. Optional metadata added later must degrade to an unavailable optional action rather than breaking generic application management.

## Security

- `cozyctl` adds no privileges and uses the caller's kubeconfig identity.
- Tenant selection never grants access or rewrites cluster RBAC.
- Discovery metadata, OpenAPI text, names, and condition messages are untrusted data and are never evaluated as shell code.
- The CLI does not shell out to `kubectl`, Helm, or dynamically discovered executables.
- Related-resource inspection cannot cross the tenant-facing API boundary.
- Local configuration contains preferences and references only, never copied credentials.

## Failure and edge cases

- **Cozystack API discovery is unavailable** → static help, version, and context commands remain usable; cluster-dependent commands identify the unavailable API group.
- **One application kind cannot be listed** → the result is marked incomplete and the command returns non-zero instead of silently omitting the kind.
- **A kind disappears between discovery and execution** → the API error is returned and the user is told to refresh the catalog.
- **Local schema differs from server admission** → server validation wins.
- **The caller lacks a requested verb** → the command reports `Forbidden` without suggesting a privilege bypass.
- **The selected tenant disappears or access is revoked** → commands fail closed and require another explicit tenant selection.
- **A watch reconnects after an API restart** → it continues only within the original timeout.

## Testing

- Unit tests cover command registration, global flags, context selection, structured output, exit-code mapping, duration parsing, and readiness evaluation.
- Discovery integration tests add and remove application kinds dynamically and assert catalog, validation, CRUD, waiting, and incomplete-result behavior without recompiling the client.
- RBAC integration tests cover tenant-admin, tenant-viewer, and unauthorized identities.
- Compatibility tests run the newest client against supported older API surfaces and verify graceful capability degradation.
- E2E creates an application from a dynamically discovered package, waits for it, inspects related resources, and deletes it using only tenant-facing APIs.

## Rollout

1. Build the shared client, discovery, output, and waiting foundation required by the stable command tree.
2. Ship catalog discovery, tenant selection, generic application get/list/create/update/delete, validation, and structured output.
3. Add readiness waiting and related-resource inspection after their metadata and RBAC behavior pass integration tests.
4. Add explicit service-specific capabilities only through separate proposals backed by concrete user workflows.

## Open questions

1. Should `tenant use` store an XDG preference or select a kubeconfig context whose namespace represents the tenant?
2. Which related-resource selectors are reliable enough to support in the first release?
3. Which service-specific action is valuable enough to justify the first explicit capability proposal?

## Alternatives considered

**Two CLIs for users and operators.** Withdrawn after review. Operator installation and upgrades require durable in-cluster state, health gates, resumability, and unattended execution; implementing that logic in a second client binary would make the workstation process the control plane. Operator-facing commands may exist later as clients of the lifecycle API, but they are outside this proposal.

**Use only `kubectl`.** This exposes Kubernetes implementation details and provides no stable application-oriented discovery and waiting workflow. `kubectl` remains the raw-resource escape hatch.

**Generate typed commands for every application.** This requires a client release for every catalog change and cannot cover application packages from external sources. Dynamic discovery matches the existing aggregated API architecture.

**Generate application types as root commands.** This makes command help, completion, and scripts depend on the connected cluster. Stable `catalog` and `app <type>` commands avoid that instability.

---

<!-- Inspired by KubeVirt enhancement proposals and Kubernetes Enhancement Proposals (KEPs). -->
