# 0007. Whoever can write a VM uses the GPUs of its class entries, while ordered claims stay with tenant admins

- **Number:** `0007`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#5030`](https://github.com/cozystack/cozystack/pull/5030); the controller-side creation and capacity check of [0012](./0012-the-vmi-webhook-checks-and-records-and-the-controller-prepares.md): not yet

## Context

The Security section gave `GPUClaim` writes to tenants with the `admin` role or above, and had the webhook create `GPUClaim` objects for `claimTemplate` entries, only of the class and count the template names. It did not say who that webhook acts for. The VMI is created by KubeVirt's virt-controller, not by the person who wrote the VM, so by the time the webhook sees it the requester is KubeVirt and the identity behind the VM is gone.

In Cozystack more identities can write a `vm-instance` than can write a `GPUClaim`. The tenant role that tenant ServiceAccounts get grants every verb on `apps.cozystack.io`, which includes `vm-instance`, and the `use` role can start and restart a VM, which creates a new VMI.

## Decision

A class entry is part of the VM, so whoever can write the VM uses its GPUs within the tenant's budget, as with the VM's CPU and memory, and whoever can start it makes them run and enter the usage records for as long as the VMI runs; in step 2 the start also makes the entry's template claims exist. The budget is checked when the VM is written (section 5), the vGPU entitlement at every VMI admission, and in step 2 capacity when the controller creates and binds the template claims; none of these checks the identity behind the VM. Ordering a `GPUClaim` directly stays with tenant admins and above.

## Why not the alternatives

- **Check the identity behind the VM at VMI admission.** The webhook sees KubeVirt's service account. The VM's author is recorded nowhere it could read reliably, and the identity that starts a VM can differ from the one that wrote it.
- **Refuse class entries in `vm-instance` for identities that cannot write `GPUClaim`.** The aggregated API that serves `vm-instance` would need a per-field authorization step for one field of one kind, and the rule would still not cover a start by a `use` role holder, who changes no field at all. The cost is a judgement call, not a hard limit.

## Consequences

- A tenant admin who hands out a tenant ServiceAccount also hands out the ability to use GPUs through class entries, up to the tenant's budget, and the `use` role lets its holder start VMs that already carry class entries. The budget, the class keys and the vGPU entitlement are the controls that limit both.
- An ordered claim, which holds GPUs and is recorded while it is `Bound` or `Attached` whether or not anything runs on it, still needs a tenant admin.

## Revisit if

KubeVirt starts recording the VM's author on the VMI in a form admission can trust, or Cozystack gains per-field authorization on application kinds.
