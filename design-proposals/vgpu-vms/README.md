# vGPU licensing for virtual machines

- **Title:** `vGPU licensing for virtual machines: the licensee, token delivery, license server reachability, usage and revocation`
- **Author(s):** `@lexfrei`

## Overview

A vGPU guest needs a license from an NVIDIA license server and a client configuration token that names that server. A token baked into a shared guest image is readable by every tenant that can clone it.

This proposal defines the licensing guidance and reference guest scripts for vGPU VMs. It builds on the accepted [license delivery contract](../gpudevice/README.md#6-vgpu-entitlement-and-license-delivery): the hoster's license is the default, and the platform delivers its token only to entitled tenants. A hoster can also offer classes for tenants that bring their own license. This proposal adds no API.

Vendor-specific settings and release requirements belong in the hoster documentation. They are checked against the NVIDIA releases the hoster runs, without amending this design whenever those releases change.

## Scope and related proposals

- [`gpudevice`](../gpudevice/README.md) owns GPU classes, entitlement, license delivery and its rotation and revocation rules. [Record 0018](../gpudevice/decisions/0018-a-vgpu-license-reaches-each-vm-as-a-secret-volume.md) records the delivery decision.
- [Kairos tenant worker pools](https://github.com/cozystack/community/pull/115) use the same Linux guest contract.
- Golden image discovery, private sources and per-tenant image visibility are outside this proposal.

## Context

Hosters already add guest images through the `cozystack.vm-default-images` Package or as CDI DataVolumes by hand. The missing guidance is about licensing those guests: whose license they use, how they reach its server, how usage maps to tenants, and what happens when licensing fails.

NVIDIA's license server is either the Cloud License Service (CLS) or a Delegated License Service (DLS) instance the hoster runs. It leases licenses to guests. A lease can remain taken after a guest stops, so the server's license count is not the number of running VMs.

## Goals

- Deliver the hoster's token only to entitled tenants, with no token in a shared image.
- Let a tenant bring its own license on a class offered for that purpose.
- Document guest setup, license server reachability, usage reconciliation and failure recovery.

### Non-goals

- Building, distributing or testing NVIDIA guest drivers or guest images.
- Running a license server or adding a billing system.
- Changing the accepted entitlement or delivery contract, or extending it to passthrough classes.

## Design

### 1. The licensee

The hoster's NVIDIA entitlement is the default. A vGPU class with a `license` supplies the hoster's token to each VM of the class in an entitled tenant. The hoster pays for the licenses and charges tenants through its own billing system.

A tenant with its own NVIDIA entitlement uses a vGPU class without a `license` and configures licensing inside its guest. It still needs the platform's vGPU entitlement. Under the current guest contract, a BYOL image does not run the reference boot unit or script: the Linux unit selects an empty token directory without the platform disk, and the Windows script clears the default token folder. Supporting both modes with one reference image would require a separate change to the accepted contract.

Both kinds of class can coexist. The hoster chooses per class; a change reaches a VM at its next start.

### 2. The token in the guest

The disk labelled `GPULICENSE` and its Linux handling are defined in [section 6 of `gpudevice`](../gpudevice/README.md#license-delivery). The reference Linux boot unit follows that contract and preserves unrelated settings in `gridd.conf`.

On Windows, the reference script empties `%SystemDrive%\Program Files\NVIDIA Corporation\vGPU Licensing\ClientConfigToken` at every boot, including boots without the disk. When the disk is present it copies the token there. After cleanup and any copy, it restarts the licensing service using NVIDIA's documented procedure. Windows keeps its other licensing settings in the registry; the disk's `gridd.conf` does not configure them. The persistent token and service ordering have the limitations described in Security.

The Cozystack documentation supplies the reference Linux unit and Windows script, without anything from NVIDIA's packages. Platform tokens are not put in images, chart values or bootstrap data.

### 3. Reaching the license server

A guest must reach the license server named by its token. The hoster chooses where that server runs and how to open the network path, including any network policy exceptions for an in-cluster DLS. Current tenant isolation rules do not prohibit that placement as an architectural choice. Without a route, a guest cannot acquire or renew a license; its behaviour follows NVIDIA's grace and degradation rules (section 6).

### 4. Usage and the license count

The platform's [usage records](../gpudevice/README.md#7-usage-records-catalog-and-licensing) describe GPU use per tenant while launcher pods exist. The license server counts licensed clients under NVIDIA's terms, including leases that outlive stopped VMs. Cozystack does not read the license server. The hoster reconciles its counts with tenant usage in its billing system; a BYOL tenant accounts for its own licenses.

### 5. Rotation and revocation

Rotation and revocation follow [section 6 of `gpudevice`](../gpudevice/README.md#license-delivery) and [record 0018](../gpudevice/decisions/0018-a-vgpu-license-reaches-each-vm-as-a-secret-volume.md). Rotation reaches a guest through a new VMI, not a live update of its disk. Clearing entitlement prevents subsequent starts, but an immediate cut-off also requires stopping the tenant's vGPU VMs.

Stuck leases can be released on the license server. Releasing a lease does not revoke a token that can check out another license. The hoster guide covers release limits, token expiry and the capacity needed during rotation.

### 6. What a tenant sees

- Without effective vGPU entitlement, a VM on a vGPU class is refused at start.
- A VM that needs a platform token waits for its namespace's license copy rather than booting without it. The platform conditions and events are defined by `gpudevice`.
- A guest that cannot acquire or renew a license follows NVIDIA's grace periods and then degrades. A degraded guest recovers full capability when it obtains a license, although applications started while degraded may need restarting.
- On a class without a `license`, the tenant licenses the guest itself.

### 7. Hoster documentation

The guide covers the reference guest scripts, licensing configuration, server connectivity, lease reconciliation and rotation. It keeps vendor ports, release limits, degradation behaviour and driver compatibility requirements with their NVIDIA sources and release context.

Host vGPU Manager upgrades are checked against the guest drivers in use. If a guest driver becomes incompatible, an entitled tenant can update the driver inside the guest. The hoster also offers an updated image for new VMs or tenants who choose to recreate theirs. The hoster checks NVIDIA's distribution terms before offering driver-bearing images.

## User-facing changes

Hosters get a licensing guide and reference scripts. Tenants on licensed classes need no manual token setup when their image follows the contract; BYOL tenants configure their own guest licensing.

## Upgrade and rollback compatibility

Rolling back `gpudevice` removes the license disk as its [rollback section](../gpudevice/README.md#upgrade-and-rollback-compatibility) describes. Linux guests following section 2 then boot unlicensed; Windows cleanup and any previously acquired license remain subject to the Security limitation below.

## Security

- A guest's administrator can read its token. Keeping it out of shared images prevents unentitled tenants from obtaining it by cloning an image, but does not prevent an entitled tenant from copying it. Server reachability restrictions and token expiry limit that exposure.
- A tenant super-admin can mount its namespace's license copy into a VM. That remains within an entitled tenant.
- On Windows, the token remains on the system disk between boots and can enter snapshots, clones and exports. The licensing service may read the previous boot's token before the cleanup script runs, including after a restart without the disk. No ordering guarantee or immediate release of a license acquired this way is established here. Cleanup and service restart do not provide the same stale-token protection as the Linux contract; rotation and revocation are weaker on Windows.
- BYOL tokens remain under the tenant's control.

## Failure and edge cases

- A VM is refused at start: check effective vGPU entitlement, including the tenant's ancestors.
- A VM waits for its license copy: check the class and the hoster's source Secret using the diagnostics in `gpudevice`.
- A guest runs unlicensed: check guest license status, server reachability, available licenses and guest configuration. Restart the license client after fixing connectivity; restart the VM to pick up a changed platform token.
- A guest keeps an old platform token: restart the VMI before the old allocation stops serving it.
- A driver fails after a host upgrade: update it inside the guest to a compatible version, or recreate the VM from an updated image.

## Testing

The platform's license copies and disk injection are tested with `gpudevice`. Reference script tests cover token placement, modes and cleanup with and without the disk, Linux service ordering, and preservation of unrelated configuration. Windows tests must cover persistent tokens and a service that has already started; they must not assume boot ordering.

Manual validation on vGPU hosts covers first licensing, rotation followed by VMI restart, refused starts without entitlement, and an unreachable license server. This requires a GPU, driver and license that CI does not provide.

## Rollout

The documentation and reference scripts build on the license delivery in item 2 of the `gpudevice` rollout. They do not wait for its host-layer step.

## Open questions

Should Cozystack read license server counts to show license use beside tenant usage, or leave reconciliation entirely to the hoster's billing system?

## Alternatives considered

The rejected delivery alternatives, including tokens in shared images and bootstrap data, are recorded in [0018](../gpudevice/decisions/0018-a-vgpu-license-reaches-each-vm-as-a-secret-volume.md).

GPU-specific golden image plumbing was dropped. Hosters already add images, a picker marking enforces nothing, and namespace-label visibility departs from the standard RBAC model. Label-based discovery belongs in a generic golden-image proposal.
