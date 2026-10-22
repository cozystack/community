# 0001. A claim whose class resource name KubeVirt does not permit is refused at VMI admission

- **Number:** `0001`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#5013`](https://github.com/cozystack/cozystack/pull/5013) (the class condition), [`cozystack/cozystack#5030`](https://github.com/cozystack/cozystack/pull/5030) (the refusal at VMI admission)

## Context

On the `default` and `vgpu` variants the platform writes a `permittedHostDevices` table into the KubeVirt CR whenever it has entries. The proposal assumed that KubeVirt refuses a GPU outside that table when the VMI is admitted. It does not. In KubeVirt v1.9.0 no admitter refuses a GPU or host-device name the table leaves out; the GraceIOVirtualization admitter reads the table only to apply its rules to names listed there, and the refusal is `validatePermittedHostDevices` in [`renderresources.go`](https://github.com/kubevirt/kubevirt/blob/v1.9.0/pkg/virt-controller/services/renderresources.go), which runs when virt-controller renders the virt-launcher pod. A render error creates no pod, and the VMI stays `Pending` with `Synchronized=False`, reason `FailedCreate`, retried with backoff. It never reaches a final phase.

That defeats the attachment rules of section 2. A claim leaves `Attached` only when its VMI is final or removed, so a claim of a class whose `resourceName` the table leaves out would stay attached to a VMI that never runs, held and in the usage records, until someone deletes the VMI by hand. A hoster gets there easily: a class is created for a new resource name before the table lists it, or the table is edited and drops an entry.

## Decision

The VMI webhook reads the effective table from the KubeVirt CR, the resource names of its `pciHostDevices` and `mediatedDevices` entries, and refuses a VMI whose claim set includes a class whose `resourceName` is not in it, with a message naming the class and the name. An unset table means KubeVirt checks nothing, and then neither does the webhook. The controller also sets a `ResourceNameNotPermitted` condition on such a class, so the hoster sees the gap before a tenant hits it.

## Why not the alternatives

- **Rely on KubeVirt's own refusal**, as the proposal did. Rejected on the mechanism above: the refusal comes after admission, as a `Pending` VMI that holds its claims, not as an error the tenant sees at start.
- **Treat a `Pending` VMI with `Synchronized=False` and `FailedCreate` as final, and collect it.** It would release the claims, but only after the tenant has started a VM that cannot run, and the reason would surface as a VMI condition rather than in the start request. `FailedCreate` is also the reason KubeVirt gives for other failures to render or create the pod, so the rule would have to parse messages to tell this one apart.
- **Refuse such a class when it is created.** A class may legitimately come before its table entry, and the table can change after the class exists, so a create-time check catches only one of the two paths.

## Consequences

- A tenant learns at start, from the webhook message, that the offer is not usable yet, and the claim stays unattached and moves by the usual rules.
- The webhook and the controller condition read the same lists of the table, so the condition predicts the refusal. KubeVirt's own check also covers `usb` entries, which no GPU class uses.
- Every VMI admission with claim references reads the KubeVirt CR, through the controller's cache.

## Revisit if

KubeVirt starts refusing a claim outside `permittedHostDevices` at VMI admission, or the table is dropped for DRA-backed devices.
