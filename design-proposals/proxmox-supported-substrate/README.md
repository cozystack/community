# Proxmox VE as a supported substrate

- **Title:** `Proxmox VE as a supported substrate: scope, support tiers and maintenance model`
- **Author(s):** `@themoriarti`
- **Date:** `2026-10-14`
- **Status:** Draft

## Overview

Cozystack v1.7 is the first release to carry a Proxmox VE substrate. With it, the workers of a tenant `Kubernetes` cluster can be Proxmox VMs provisioned through Cluster API, and the Proxmox cloud-controller-manager and CSI driver run for the tenant from the management cluster. It arrived as a series of opt-in pull requests.

[cozystack/cozystack#3481](https://github.com/cozystack/cozystack/issues/3481) asks the question that series did not answer: does Proxmox become a supported way to run Cozystack, meaning that someone maintains it, tests it and answers issues about it for as long as it ships? It also asks for that decision to be made deliberately, not accumulated by merging parts.

This proposal asks for that decision in a form that can be checked later. It defines:
- what "supported" covers;
- the support tiers;
- the owners and what they commit to;
- the test lane that backs the claim;
- the conditions for moving up a tier and, just as importantly, back down.

It also settles how the substrate relates to [community#86](https://github.com/cozystack/community/pull/86), the per-cluster backend model. Proxmox is the first non-KubeVirt backend with code in the tree. This proposal commits to moving it onto #86's library and backend contract once #86 is accepted, and offers it as a second worked example of that contract.

The ask: ship Proxmox in v1.7 as **Beta**, and promote it to **Supported** once the promotion criteria below hold. One of those criteria is that the user-facing shape is settled under #86.

## Scope and related proposals

- **[community#86](https://github.com/cozystack/community/pull/86), per-cluster infrastructure backends.** It rejects "one `kubernetes` chart with a backend field", which is how the Proxmox substrate landed (`substrate: kubevirt | proxmox` in `kubernetes` and `kubernetes-nodes`). This proposal does not argue for keeping that field. It commits to porting Proxmox onto #86's library and backend contract (§7), and fills in the Proxmox column of #86's contract table (Context) so the gaps are written down now.
- **[community#93](https://github.com/cozystack/community/pull/93), Proxmox-backed VPC subnets** (being split as requested in review). Out of scope; it stays Experimental until its own proposal is accepted.
- **A management cluster without KubeVirt.** Today the only variant of `cozystack.kubernetes-application` depends on the KubeVirt Cluster API provider. Under #86 a backend is its own `PackageSource` with its own `dependsOn`, which removes that coupling. This proposal does not add a separate mechanism for it.
- **Conformance.** The lane described here is the shape that would later run the Conformance Suite on Proxmox for a "Certified Cozystack Provider" entry ([ROADMAP §6](https://github.com/cozystack/cozystack/blob/main/ROADMAP.md#6-cozystack-conformance-program)). Nothing here depends on that program.

## Decisions

## Context

### What is in the tree

All of the following is in `main` and `v1.7.0-rc.1`, except the last row, which is an open pull request.

| Piece | Where | Pull request |
|---|---|---|
| capmox v0.7.7 and in-cluster IPAM v1.0.3 as optional packages, sha256-pinned components, bundle wiring through `bundles.enabledPackages` | `packages/system/capi-providers-infraprovider-proxmox`, `packages/system/capi-providers-ipam-in-cluster`, `packages/core/platform` | cozystack#4087, #4483 |
| `substrate: kubevirt \| proxmox` (immutable) and the `proxmox.*` block on `Kubernetes`; `ProxmoxMachineTemplate` pools on `KubernetesNodes`; render-time guards | `packages/apps/kubernetes`, `packages/apps/kubernetes-nodes` | cozystack#4088 |
| Proxmox CCM and CSI controller in the management cluster, in the tenant's namespace; node plugin, CSIDriver and storage classes in the tenant; pre-delete volume cleanup | `packages/apps/kubernetes/templates/{ccm,csi}-proxmox`, `packages/system/proxmox-csi` | cozystack#4089 |
| Worker template built from the pool's Talos values | `hack/proxmox-talos-template.sh` | cozystack#4742 |
| E2E lane, parked: workflow, Chainsaw suite, harness | `.github/workflows/e2e-proxmox.yaml`, `hack/e2e-chainsaw/kubernetes-proxmox/` | cozystack#4089, #4807 |
| CCM v0.16.1 and digest pins for the Proxmox images | `packages/apps/kubernetes/images/proxmox-*.tag`, `packages/system/proxmox-csi/values.yaml` | cozystack#4917 (open, labelled for backport) |

The credential model is in `packages/apps/kubernetes/README.md` ("Proxmox substrate: credentials, privileges and tenant scope"). The hypervisor token lives only in the management cluster. Each tenant is scoped by one ACL on a Proxmox resource pool, and capmox places every worker VM into that pool. This already matches #86 §5: credentials come by Secret reference in the tenant namespace and are never copied into the tenant cluster.

### What has been verified, and how

Unit coverage runs on every pull request: `substrate_proxmox_*` helm-unittest suites in `kubernetes`, `kubernetes-nodes` and `platform`, plus bats tests under `hack/`. Everything end-to-end has been verified by hand on one stand: Proxmox VE 9.2.20, Talos v1.13.6, Kubernetes v1.35.6, two tenant clusters, one of them carrying production traffic. On 2026-10-14:
- **CCM v0.16.1.** A new worker's Node was initialised 47 ms after it registered. Its providerID `proxmox://<SMBIOS UUID>` was identical on the Node, the Machine, the ProxmoxMachine and the VM ([cozystack#4917 comment](https://github.com/cozystack/cozystack/pull/4917#issuecomment-6165361669)).
- **Worker template.** `hack/proxmox-talos-template.sh` built a worker template from the chart's own Talos values, and three node pools were rolled onto it. On every new VM the guest agent that the chart's schematic waits for answered within a minute of creation.
- **Cluster API v1.13.7.** These were the first Machines created on the stand under Cluster API v1.13.7 (cozystack#4902). They were provisioned and joined normally.

The E2E lane has never run. It is gated on `vars.PROXMOX_NIGHTLY`, it targets a `[self-hosted, proxmox]` runner that does not exist, and the Chainsaw suite is still `.disabled`. The stand still runs these charts from a fork line rather than from an upstream release.

### The Proxmox substrate against the backend contract (#86 §3)

| Responsibility | Proxmox substrate today | Status |
|---|---|---|
| Infra cluster and machine template | `ProxmoxCluster`, `ProxmoxMachineTemplate` (capmox v0.7.7), linked clones of a tagged template | covered |
| Node `providerID` | Proxmox CCM in the management cluster, `features.provider: capmox`, writes the same `proxmox://<SMBIOS UUID>` that capmox puts on the Machine | covered |
| Storage: CSI driver and default storage class | Proxmox CSI: controller in the management cluster, node plugin and storage classes (`WaitForFirstConsumer`, region/zone topology) in the tenant | covered; volumes on storage shared between tenants are not isolated (Security) |
| `Service type: LoadBalancer` | none: the CCM runs only `cloud-node` and `cloud-node-lifecycle`, and kccm is off on this substrate | **not covered**; the schema does not say so |
| Ingress exposure methods | `Proxied` selects virt-launcher pods (`templates/ingress.yaml`, no substrate branch), so it has no endpoints here; `LoadBalancer` needs the row above | **not covered**; the schema still offers both |
| API server endpoint for workers and Cilium | `TenantControlPlane` Service is `LoadBalancer` on this substrate; workers and Cilium (`k8sServiceHost`) use `<release>.<namespace>.svc`, mapped to that address by Talos `extraHostEntries` | covered |
| In-cluster API path (`kubernetes.default.svc`) | resolves through the tenant Service to the same address | covered (works on the stand) |
| Talos trustd and konnectivity reachability | ports 50001 and 8132 on the same `LoadBalancer` Service | covered |
| Management-side services used by addons (monitoring) | agents write to `*.svc` names of the management cluster; workers resolve through `proxmox.dnsServers` | **not verified**, likely not covered |
| Machine-config specifics | nameservers from `proxmox.dnsServers`, `cloud-provider: external`; `install.disk: /dev/vda` is unconditional, while a virtio-scsi disk is most likely `/dev/sda` | partly; the install disk matters only to an in-place Talos upgrade, which this substrate does not do (machines are replaced) |
| Cluster-autoscaler RBAC | the Role lists `kubevirtmachinetemplates` only | **gap**: no scale from zero; scale from one is untested |
| Teardown | pre-delete hook deletes PVCs and waits for the volumes to go before the CSI controller is removed; infra objects go with the CAPI `Cluster` | covered |

### The problem

- **No stated support level.** The substrate is in a release and the README documents it. Nothing tells a user whether a Proxmox issue will be answered, whether the next minor release will still carry it, or whether anyone ran it before tagging.
- **No test behind the claim.** This release cycle showed what that costs. An upstream re-push of the CCM's `v0.15.0` tag replaced a working build with one that panics on start. A running stand did not notice for five days, and `v1.7.0-rc.1` shipped that tag (cozystack#4917 fixes it). A scheduled lane would have failed on its next run.
- **API shape not settled.** The substrate field is a shape #86 rejects, so promising compatibility for it now would be promising something the project may not keep.

## Goals

- Each release states the Proxmox substrate's support tier in the changelog and in the documentation.
- A scheduled E2E lane runs on `cozystack/cozystack` against real Proxmox VE, with public results, and is shown to fail when the integration is broken.
- Named owners carry the area in `CODEOWNERS` and commit to a response time on `area/proxmox` issues and pull requests.
- The Proxmox backend's coverage of #86's contract is written down, and every uncovered row is either covered or declared unsupported in the schema.
- The criteria for promotion and demotion are observable from GitHub alone: workflow runs, issue timestamps, `CODEOWNERS`.

### Non-goals

- Making volumes on shared storage isolatable between tenants. That is a property of Proxmox VE storage; the documented answer is storage per tenant.
- Supporting Proxmox VE versions the lane does not run against.
- LXC containers, Proxmox SDN, or Proxmox-side HA of worker VMs.
- Networking beyond a bridge and VLAN on the pool (community#93).
- Designing the backend model. That is #86; this proposal only commits Proxmox to it.

## Design

### 1. Support tiers

| Tier | What it promises |
|---|---|
| **Experimental** | Code is in the tree and opt-in. No compatibility promise; may change or be removed in any minor release. Issues answered on a best-effort basis. |
| **Beta** | Opt-in, documented, unit-tested on every pull request, E2E lane in place. Breaking changes are allowed in a minor release with a changelog entry and an upgrade note, the move onto #86 included. Owners respond to `area/proxmox` issues within the response time below. |
| **Supported** | Everything in Beta, plus the promotion criteria in §5. Breaking changes only with a migration path. Regressions found by the lane block the release that would ship them. |

Proxmox enters v1.7 as **Beta**.

### 2. Supported topology and versions

| Dimension | Supported | Notes |
|---|---|---|
| Management cluster | A standard Cozystack install with the Proxmox packages added through `bundles.enabledPackages` | Without KubeVirt only after the move onto #86 (Scope) |
| Proxmox VE | 9.2.x | The version the lane runs against. Other 9.x minors become supported when the lane runs on them. 8.x is not supported. |
| Talos (workers) | The chart's `talos.version` | Template built by `hack/proxmox-talos-template.sh`, which reads the same values the pool renders |
| capmox / IPAM / Cluster API | As vendored by the release | Bumps go through the lane before they merge |
| Storage | Proxmox VE storage reachable from the worker's node | Storage per tenant is the documented configuration |

### 3. Ownership

- **Owners.** @themoriarti (CageForge) and a second owner from a different organisation (see Open questions). Two owners make "for as long as it ships" independent of one person's or one company's availability.
- **CODEOWNERS.** Owners are added beside the maintainers on the Proxmox paths that do not mix substrates:
  - `packages/system/capi-providers-infraprovider-proxmox/`, `packages/system/capi-providers-ipam-in-cluster/`, `packages/system/proxmox-*/`;
  - `packages/apps/kubernetes/templates/ccm-proxmox/`, `packages/apps/kubernetes/templates/csi-proxmox/`, `packages/apps/kubernetes/templates/helmreleases/csi-proxmox.yaml`, `packages/apps/kubernetes/images/proxmox-*.tag`;
  - `hack/e2e-chainsaw/kubernetes-proxmox/`, `hack/proxmox-*`.

  Files that render both substrates and the workflow stay with the maintainers, and owners are requested as reviewers on changes to their Proxmox branches. After the move onto #86 the backend package becomes the owners' area as a whole.
- **Triage.** The labeler maps the title scopes `proxmox` and `capmox` to the existing `area/proxmox` label. Owners give a first response to `area/proxmox` issues and pull requests within **two working days**, the target GOVERNANCE.md sets for new contributors.
- **Time.** Each owner commits at least **10 hours a month** to the area.
- **Upstream dependencies.** Owners watch capmox, proxmox-cloud-controller-manager and proxmox-csi-plugin releases. Every third-party image is pinned as tag plus digest, and a version bump is a pull request that has passed the lane.

### 4. The E2E lane

The workflow and the suite exist. What is missing is somewhere to run them, and a few assertions.

- **Runner.** CageForge provides the self-hosted runner and pays for it.
  - It is registered to an organisation runner group restricted to `.github/workflows/e2e-proxmox.yaml`.
  - The workflow runs it only on `schedule` and on `workflow_dispatch` against `main` and `release-*`, never on `pull_request`, so code from a fork never reaches it.
  - Each run gets a fresh VM, and the Proxmox token is scoped to a pool dedicated to the lane.
  - The harness talks to the Proxmox API instead of SSH, so the runner holds no hypervisor root key.
  - The schedule avoids the host's maintenance window.
- **What it asserts.** It keeps what the suite asserts today: `ProxmoxCluster` Ready, Machines Running from a linked clone, the CCM setting a providerID that matches the Machine, a PVC bound and written, the disk freed on teardown, and a replaced Machine rejoining. It adds:
  - the worker template's guest agent answering;
  - MHC remediation on a pool that allows it, triggered by taking the VM's network link down (capmox restarts a stopped VM);
  - an upgrade within the release line;
  - a scan of every Secret in the tenant for the hypervisor token.
- **Proof it can fail.** Before the lane counts towards promotion, it is shown red on three deliberate breakages:
  - the CCM back in its default provider mode, so the providerID no longer matches the Machine;
  - a blanket toleration on the CCM, so a worker replacement hangs;
  - a CCM image that does not start, so no new Node is initialised.
- **Results.** Runs are public under the workflow, and the documentation links to them.

### 5. Promotion to Supported

Promotion is decided by the maintainers when all of these hold:

1. The lane has 14 consecutive green scheduled runs on `main`.
2. No `area/proxmox` issue has waited longer than 14 days without a response.
3. Every Proxmox image is pinned by digest (cozystack#4917).
4. The documentation has an enablement guide, the support matrix from §2, the known limitations, upgrade notes, and troubleshooting for the failure modes seen so far (uninitialized Nodes, a worker template whose guest agent never comes up).
5. Alerts for the capmox, IPAM, CCM and CSI controllers ship with their charts. A crash-looping CCM is exactly what went unnoticed for five days.
6. Both owner seats are filled.
7. At least one organisation runs the substrate in production from an upstream release and is listed in `ADOPTERS.md`.
8. Every row of the contract table above is covered or declared unsupported. A declared row is one whose dependent options the schema no longer offers: `Proxied` ingress, `Service type: LoadBalancer`, monitoring agents until verified. The autoscaler Role covers `proxmoxmachinetemplates`. The chart documentation describes only what the tree ships; today the `proxmox.full` description refers to a consolidation CronJob for linked clones that the tree does not include.
9. The shared VM-provisioning token (Context) is either fixed or accepted by the maintainers as a documented limitation. Fixing it means capmox precedence for `credentialsRef`, then a per-tenant reference from the chart.
10. The user-facing shape is settled: Proxmox runs as a backend package under an accepted #86, or the maintainers decide to keep the substrate field.

### 6. Demotion

The substrate drops one tier in the next minor release, with a changelog entry and a banner on its documentation, when any of these holds:

- the lane has been red or skipped for four consecutive weeks without an owner's fix or a linked issue explaining it;
- no owner has acted on an `area/proxmox` issue or pull request for three months, the inactivity threshold in CONTRIBUTOR_LADDER.md;
- an owner seat has been empty for three months while Supported.

Demotion does not remove code. Removal is a separate maintainer decision, deprecated over at least one minor release. The maintainers can demote at any time through the usual decision process.

### 7. Moving onto #86

Once #86 is accepted and its library and addon source land (its Rollout steps 1–4), the owners port Proxmox the way #86 describes for OpenStack:

- **Packages.** A Proxmox `PackageSource` with a control-plane application and a node-pool application, rendered from the shared library plus `ProxmoxCluster` / `ProxmoxMachineTemplate`, the management-side CCM and CSI controller, and the tenant-side CSI release.
- **Dependencies.** `dependsOn` lists capmox and the in-cluster IPAM, not the KubeVirt provider.
- **Contract.** The contract rows from Context, with unsupported rows removed from that backend's schema.
- **Migration.** A path from `Kubernetes` with `substrate: proxmox` to the backend's kinds. The substrate field stays until that path ships, and is then deprecated over at least one minor release.

The Proxmox column above is offered to #86 as a second worked example of its contract, next to OpenStack.

## User-facing changes

- A Proxmox section in the documentation with the tier, the support matrix, the contract coverage and the limitations.
- A "Proxmox substrate" line in every release's changelog stating its tier.
- No API change from this proposal itself. The move onto #86 (§7) changes the kinds, with a migration path.

## Upgrade and rollback compatibility

Tiers change promises, not manifests, so nothing rendered changes. Demotion leaves existing clusters running and only changes what the project commits to. The #86 port in §7 carries its own migration and deprecation window.

## Security

- **The runner.** It is a new trust boundary: project credentials on hardware the project does not own. Mitigations are in §4: a restricted runner group, no `pull_request` trigger, a fresh VM per run, a pool-scoped token, no hypervisor SSH key. Lane credentials are repository secrets managed by the maintainers.
- **Shared storage.** It cannot isolate tenants' volumes from each other, because every CSI volume has the same owner VMID, and the privilege to delete one is the privilege to attach any. The documentation states this, recommends storage per tenant, and says what a tenant with access to shared storage can reach.
- **One provisioning token for all tenants.** capmox v0.7.7 uses `ProxmoxCluster.credentialsRef` only when no global client is configured, and the package configures one. The token never reaches a tenant cluster; the capmox controller in the management cluster holds it.
- No new tenant-supplied input.

## Failure and edge cases

- **An upstream re-pushes a tag.** The digest pin keeps the old bytes; the next intended bump is a reviewed pull request that passes the lane.
- **The runner host is down.** Scheduled runs fail or skip; four weeks of that triggers demotion.
- **A Cluster API, capmox or Kamaji bump breaks Proxmox.** The lane fails on the bump's pull request when dispatched, or on the next scheduled run. The bump does not ship in a release until it is fixed or reverted.
- **Proxmox VE 10 is released.** It is unsupported until the lane runs on it.
- **An owner leaves.** The second owner carries the area; an empty seat for three months triggers demotion.
- **#86 is rejected or stalls.** Criterion 10 then needs an explicit maintainer decision on the substrate field; until then the tier stays Beta.

## Testing

- **Unit**, on every pull request (`make unit-tests`): helm-unittest suites `substrate_proxmox_*` in `kubernetes`, `kubernetes-nodes` and `platform`; bats tests for the template script, the E2E fixture, the pre-delete matcher, the vendored provider components and the CSI driver pins.
- **E2E**, scheduled and on dispatch: the lane in §4, against Proxmox VE 9.2.x.
- **Manual**, before promotion: the three deliberate breakages in §4, recorded in the promotion pull request.

## Rollout

1. **v1.7.0**: ships as Beta. cozystack#4917 is backported to `release-1.7` before GA. A changelog entry states the tier and limitations.
2. **Before v1.8**:
   - register the runner and enable the lane;
   - update CODEOWNERS and the labeler;
   - publish the documentation and the contract table;
   - ship the alerts in the charts;
   - remove the uncovered options from the schema.
3. **After #86 is accepted**: the port in §7.
4. **First minor release after §5 holds**: a promotion pull request with the evidence for each criterion. Maintainers decide.

## Open questions

1. **Second owner.** Who, from which organisation? Without one, criterion 6 cannot hold.
2. **Runner registration.** Do the maintainers accept a self-hosted runner under the constraints in §4, and who registers it in the organisation?
3. **#86 timing.** Should promotion wait for #86 (criterion 10), or would the maintainers rather accept the substrate field as the supported API for the 1.x line and port later?
4. **Provisioning token.** Fix before Supported, or accept as a documented limitation (§5.9)?

## Alternatives considered

- **Decide "no".** Remove the substrate before v1.7.0 GA, or leave it Experimental indefinitely. This is honest and cheap, but it discards an integration that works on a live cluster, has an owner offering to carry it, and that people have asked about for two years. Leaving it in the tree with no tier is the outcome #3481 calls drifting.
- **Supported immediately in v1.7.** The lane has never run, so the claim would rest on manual checks of one stand. The CCM tag incident is what that looks like in practice.
- **Keep the substrate field as the permanent shape.** It works and it is in the tree, but it is the "backend field in one chart" that #86 rejects with reasons that hold here too: every backend lands in the core chart, and the chart carries every provider's dependencies. It also keeps the KubeVirt dependency on a Proxmox-only management cluster.
- **Keep it outside the tree** as an external package catalogue. Before #86 that means forking `kubernetes` and `kubernetes-nodes`, the drift #86 documents for `kubernetes-switchcloud`. After #86 an out-of-tree backend becomes possible, but the lane and the owners would then sit outside the project's release gate.
- **Fork-only E2E, with results reported back from a fork.** The runner would never see the code about to be released, and nothing in `cozystack/cozystack` would fail when Proxmox breaks.
