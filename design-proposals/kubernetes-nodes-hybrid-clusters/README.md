# Hybrid kubernetes clusters: workers in external environments

- **Title:** `Hybrid kubernetes clusters: workers in external environments`
- **Author(s):** `@kvaps`
- **Date:** `2026-05-04`
- **Status:** Draft (deferred)

## Overview

This proposal is a **placeholder for Phase 3** of the kubernetes-application reshape. The detailed design will be filled in only after the preceding work lands.

**Prerequisite**: [`Migrate kubernetes workers to Talos and split control-plane from node pools`](../kubernetes-nodes-split/) (PR #8) — which delivers Phase 1 (Talos migration) and Phase 2 (package split). Nothing in this Phase 3 proposal is meaningful before that lands.

## Scope (intended)

Hybrid kubernetes clusters: workers that live **outside** the Cozystack management cluster. Concretely, the use cases that should drive this design:

- **External cloud workers**: a tenant cluster running its control-plane in Cozystack (Kamaji) but its worker nodes as cloud VMs in Hetzner, Azure, AWS, GCP, etc. Driven by `cluster-autoscaler` with the cloud's native provider, not by CAPI.
- **BYO clusters**: tenants who bring their own cloud account and want their pool to be billed against that account rather than the Cozystack platform's. Implies admin-managed *or* tenant-managed location ownership.
- **Bare metal / on-premise workers**: a tenant wanting nodes in their own datacenter joined to a Cozystack-hosted control-plane.

The Novolos use case is the concrete driving example: workers in different tenant clouds, each with their own `cluster-autoscaler`, all joining a single managed Kamaji control-plane.

## Why deferred

Three reasons:

1. The package split delivered by Phase 2 (PR #8) is the architectural seam Phase 3 needs. Designing external backends before the split is in place forces shoehorning them into the monolithic `kubernetes` chart's `nodeGroups`, which doesn't fit semantically and burns design effort that Phase 2 reclaims.
2. The Talos worker base delivered by Phase 1 (PR #8) is what makes external workers tractable in the first place. Ubuntu + kubeadm joining a remote Kamaji cluster is operationally awkward; Talos + machineconfig over cloud-init is the path of least resistance for both KubeVirt VMs (in-cluster) and cloud VMs (external).
3. Several open Cozystack-side decisions (admin- vs tenant-owned location ownership, credential model for BYO clouds, default deny vs explicit advertise, dashboard surfacing) are best made with concrete Phase 1 + 2 operational experience in hand, not in advance.

## Sketches (non-committal, for orientation)

Several patterns were raised during early discussion of PR #8. They are listed here so the conversation does not restart from zero when work resumes, but **none of them is committed**.

- **New `backend.type` field in `kubernetes-nodes`.** The single-backend "kubevirt-talos" shape from Phase 2 grows a discriminator: `kubevirt-talos`, `cloud-talos-hetzner`, `cloud-talos-azure`, etc. Per-backend sub-charts realise the actual lifecycle (CAPI for KubeVirt-VM backends; `cluster-autoscaler` directly against the cloud's native API for cloud backends).
- **`LocationProfile` CRD** declaring "how to provision in a given location" — credentials, base image reference, region. Owned by the platform admin in `cozy-system` or, optionally, by the tenant in their own namespace (Novolos-style BYO).
- **Node-lifecycle controller (NLC)** from `cozystack/local-ccm` deployed inside the tenant cluster to remove zombie `Node` objects when `cluster-autoscaler` deletes a cloud VM. For CAPI-backed pools (KubeVirt) NLC is not needed; the CAPI machine controller already cleans up the `Node`.
- **Talos image source per backend**: pre-baked snapshot/VHD for cloud providers, KubeVirt-friendly disk image for KubeVirt-VM workers. Defined in the `LocationProfile` (or equivalent) so tenants reference rather than describe.
- **Scheduling-class semantics**: `tenant.spec.scheduling` continues to apply to `kubevirt-*` backends (real VMs scheduled in the management cluster). For `cloud-talos-*` backends it does not apply — there is no management-cluster pod. Equivalent tenant-scoping for cloud backends moves to RBAC on `LocationProfile` (or equivalent).
- **Credentials for BYO cloud**: tenant-namespace Secret referenced by the tenant's own `LocationProfile`. The management-cluster `cluster-autoscaler` for that pool mounts the Secret over the existing kubeconfig path, so credentials never escalate to platform level.

## Out of scope (always)

- Anything that should land in Phase 1 or Phase 2 of PR #8.
- Worker bootstrap mechanisms other than Talos. Ubuntu + kubeadm is removed in Phase 1 and not revisited.
- Removing Cluster API entirely. For KubeVirt-VM workers, CAPI + CAPK remains the path. For cloud-VM workers, CAPI is bypassed (autoscaler + native cloud provider), but this is per-backend and does not imply CAPI removal elsewhere.

## Open questions

Will be filled in when work resumes. Initial set, for orientation:

- Admin vs tenant ownership of locations: which is the default, are both supported, what does RBAC look like?
- One `LocationProfile` per (location, backend) combination, or one per location with per-backend fields?
- Pool-level vs Location-level credentials? (Per-pool gives finer granularity; per-Location is simpler.)
- Naming conventions for cloud-backend pools, especially when many locations exist.
- How `Cilium` and the Cozystack networking layer (Kilo mesh, externalIPs CCM) interact with cloud-VM workers — what overlay/encryption is mandatory.

## Status and next steps

This document is held in draft pending PR #8 landing. Once Phase 1 + Phase 2 ship and are operationally stable, this draft is the entry point for restarting Phase 3 design work.
