# Ephemeral VM sessions

- **Title:** `Ephemeral VM sessions (VMSession)`
- **Author(s):** `@kvaps`
- **Date:** `2026-06-30`
- **Status:** Draft

## Overview

This proposal introduces an on-demand, short-lived isolated VM created by cloning an existing VM. A new resource — working name `VMSession` — clones a designated "master" VM as-is into a fresh, isolated VM, starts it, and reclaims it when the session ends. You keep one VM as the master, configured however you like (image, tooling, packages); each session is a disposable, full clone of it.

The goal is to offer per-user / per-workspace / per-session isolated environments as a first-class capability, instead of every product hand-rolling clone-and-lifecycle on top of the VM primitives.

## Context

Teams building developer-facing products repeatedly need to hand an end user a freshly isolated Linux box, on demand, and throw it away afterwards:

- **AI coding agents** — each workspace runs *untrusted, machine-generated* code: read/write files, install dependencies from the public internet, run builds and tests. This needs a real escape boundary — the box must not reach the host, the orchestrator, the container runtime socket, internal networks, secrets, or other workspaces.
- **VDI** — a user connects, gets a clone of a desktop master; on disconnect it is destroyed.
- **Interactive playgrounds** (Katacoda/Killercoda-style) — a scenario clones a prepared master, accessible from the browser, destroyed after a time box.

These are different products with one shared engine: an ephemeral, isolated VM cloned from a master, created on session start and reclaimed on session end.

### The problem

Cozystack already ships the building blocks — `vm-disk`, `vm-instance`, CSI disk cloning, tenant-level network isolation — but there is no single resource that ties them into "give me a throwaway clone of this VM and clean it up afterwards". Each consumer reimplements the clone, the boot, the lifecycle, and the teardown.

## Goals

- A single resource that clones a designated VM and runs the clone on demand.
- Start the clone even when the master is kept stopped as a template.
- Reclaim the clone and its ephemeral disks on teardown.
- Build on existing primitives; minimize net-new machinery.
- Strong isolation by default — the VM boundary plus the existing tenant network policy.

### Non-goals

- The in-VM contract (file access, shell/exec, the agent) — that stays the consuming product's responsibility.
- Warm, sub-second RAM resume with frozen processes — a possible later extension, not part of this proposal.
- Cluster-level or multi-node ephemeral environments — out of scope; this is VM-granularity only.

## Design

### The isolation boundary

The unit of isolation is a virtual machine — its own kernel behind a hardware-virtualization (KVM) boundary, native on bare-metal Cozystack hosts with no nested virtualization. This is microVM-grade and self-hosted / EU-deployable. Container/namespace sandboxes (bubblewrap, even gVisor) are deliberately treated as a weaker boundary; the VM is what this design relies on.

### Mapping to existing primitives

The clone-and-run loop is already expressible:

- `vm-disk` with `source.disk: <master>` produces a CSI fast-clone of the master's disk.
- `vm-instance` referencing the cloned disk, with `running: true`, boots a VM just like the master — even if the master itself is stopped.
- On teardown, the clone is deleted.

`VMSession` is the thin wrapper that performs exactly this from a single master reference and reclaims the clone on delete. KubeVirt also ships a native `VirtualMachineClone` that copies a whole VM object at once — an alternative engine under the hood.

### Lifecycle and desired state

`VMSession` carries a declarative desired state — `spec.state: Running | Paused | Stopped` — and the controller reconciles the underlying VM to it:

- **Running** — the clone runs (`vm-instance` `runStrategy: Always`).
- **Paused** — the guest is frozen via KubeVirt's `pause` subresource: vCPUs stop and resume is near-instant. Note that pause keeps the guest RAM resident on the node — node resources are *not* freed. Good for short idle with instant resume, not for scale-to-zero.
- **Stopped** — the VM is halted (`runStrategy: Halted`): node resources are freed, but the guest cold-boots on the next start; process and RAM state are lost, while the disk (and any persistent workspace volume) survive.

Because pause is a KubeVirt subresource rather than a spec field, the controller invokes `pause`/`unpause` to drive the `Paused` state and uses `runStrategy` for `Running`/`Stopped`.

**Limitation — warm resume.** Stable KubeVirt has no suspend-to-disk that both frees node resources *and* restores RAM/process state (a Firecracker-style snapshot). `VirtualMachineSnapshot` captures disk only; `memory-dump` is diagnostic and not restorable. So today `VMSession` offers fast-resume-but-resources-held (`Paused`) or resources-freed-but-cold (`Stopped`), not both. True warm, scale-to-zero resume with live processes is a gap that would need upstream KubeVirt work or an external CRIU-style layer; it is out of scope for v1 and tracked as a follow-up.

### Reachability

The session VM is reached through the VM's own Service: `vm-instance` exposes ports with `external: true` and `externalPorts` (e.g. `[22]` for SSH, with public keys injected via cloud-init `nocloud`). A browser web-terminal is just another exposed port.

### Persistence (optional)

"Clone as-is" is stateless by default — the clone dies with the session. If a workspace must persist across sessions, its files live on a separate disk that outlives the clone and is re-attached to the next session's clone. This gives resume semantics — the project is where you left it — without a snapshot resource.

## User-facing changes

A tenant creates a `VMSession` (or, in the alternative shape below, references a `VMTemplate` from a `VMInstance`), pointing at a master VM, and gets back a running, isolated clone with a known entry point. `spec.state` (`Running` / `Paused` / `Stopped`) controls the clone's lifecycle, and `spec.networkIsolation` opts the session into per-session network rules. Deleting the session reclaims the clone. There is no change to existing VM workflows; the capability is additive and opt-in.

## Upgrade and rollback compatibility

Additive: a new, optional resource. Existing clusters, manifests, and the current `vm-instance` / `vm-disk` apps keep working unchanged. Removing the feature leaves existing VMs untouched; only sessions, which are ephemeral by definition, are affected.

## Security

The clone runs behind a VM (KVM) boundary and inherits Cozystack's tenant-level Cilium isolation, because it is a pod in the tenant namespace: by default a tenant may egress to the public internet (`toEntities: world`) but not to other tenants or arbitrary cluster pods, and the kube-apiserver is unreachable unless a workload is explicitly labelled `policy.cozystack.io/allow-to-apiserver`.

Per-session isolation ships inside the `VMSession` chart as a `CiliumNetworkPolicy` scoped to the session's VM (an `endpointSelector` on the session label), toggled by `spec.networkIsolation`. It allows egress to the public internet and DNS, and uses Cilium **deny rules** (`egressDeny` / `ingressDeny`) to block private and internal ranges (RFC1918, link-local), the cluster pod/service CIDRs and the kube-apiserver, and other sessions in the same tenant (A↔B).

This holds regardless of the blanket-allow tenant baseline, because Cilium deny rules take precedence over allow rules — so the policy enforces "internet yes; private/internal/metadata/other-sessions no" without depending on a platform-wide default-deny baseline. Shipping the policy in the chart follows existing precedent: the tenant chart and the `cilium-networkpolicy` system package already ship CiliumNetworkPolicies, the latter using `ingressDeny`.

Accepted residual risk: a session can exfiltrate its own data over the permitted public path — inherent to "internet access is required".

VMs are configured via cloud-init `nocloud` (user-data and SSH keys delivered through a Secret mounted as a disk); there is no metadata IMDS endpoint in this architecture.

## Failure and edge cases

- Master is stopped → the session clone is still created and started (`running: true`).
- Master is running while cloned → the clone is crash-consistent at best; clean clones expect a stopped or quiesced master (see open questions).
- Session deleted → the clone and its ephemeral disks are garbage-collected; a separate persistent workspace disk, if used, is retained.
- Two sessions in one tenant → reachable to each other today under the tenant-scoped policy; A↔B isolation is an open design point.

## Testing

A manual vertical slice on the existing apps first: clone a master's disk → boot a `vm-instance` with SSH exposed → install dependencies and build → verify the boundary (the kube-apiserver, internal services, and another tenant's session are unreachable; the public internet works) → tear the clone down (optionally re-attach a persistent workspace disk for stateful resume) → measure spin-up. Once the loop holds, the `VMSession` controller gets unit and e2e coverage for clone, start, teardown/GC, and policy.

## Rollout

1. Prototype the loop on existing apps with no new CRD, validating isolation and spin-up.
2. Introduce the chosen resource shape (`VMSession`, or `VMTemplate` + `templateRef`).
3. Layer optional persistence and, if wanted, cross-tenancy or a warm pool.

## Open questions

1. **Entity shape.** `VMSession` cloning an existing VM, or `VMTemplate` + a `templateRef` on `VMInstance` (see *Alternatives considered*)?
2. **Placement / A↔B granularity.** The chart-shipped per-session `CiliumNetworkPolicy` (deny rules) isolates A from B within a shared tenant directly; is that enough, or is one tenant/namespace per session still wanted as a coarser, simpler boundary?
3. **Cross-tenancy.** Do we need it at all — a master/template in one tenant cloned into sessions in other tenants, or a single source shared across tenants — and if so, how to implement it (cross-namespace clone permissions, a `cozy-public`-style shared catalog, or sub-tenants)?
4. **Spin-up latency.** Cold clone+boot is seconds; acceptable per session, or is a pre-warmed pool wanted?
5. **Running source.** Is cloning a running master/template (crash-consistency) needed, or is a stopped one enough?
6. **Warm resume.** Is `Paused` (instant resume, but node resources stay held) enough, or is true scale-to-zero warm resume with live processes required — which is not available on stable KubeVirt and would need upstream or CRIU-style work?

## Alternatives considered

- **`VMTemplate` + a reference from `VMInstance`.** Instead of a session resource cloning a live VM, introduce a `VMTemplate` holding the VM settings (instance type, networking, base disks) and a `templateRef` on `VMInstance` that takes its settings from the template and clones the template's disks on creation. This gives a clean separation between an (almost) immutable template and the running clones, closer to VMware-template / image-and-flavor models, and keeps cloning inside the existing `VMInstance` rather than adding a session lifecycle object. The trade-off is a new template concept and a schema change to `VMInstance`, versus `VMSession` where the master is simply an existing VM you can boot and edit in place. Either shape can carry the ephemeral lifecycle; the choice is about whether the source of truth is a dedicated template or an existing VM.
- **Container / userspace sandboxes (bubblewrap, gVisor, Kata).** Weaker or operationally heavier isolation than a full VM; gVisor's userspace kernel and Kata's nested-virtualization requirement make them a worse fit for a strong, bare-metal, no-nesting boundary.
- **Managed microVM offerings (Firecracker-based).** Clean isolation, but not self-hosted and not EU-deployable; this proposal targets a self-hosted equivalent on Cozystack's existing KubeVirt-on-bare-metal substrate.
