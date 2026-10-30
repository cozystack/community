# Node fencing: restart workloads of a dead node automatically

- **Title:** `Node fencing and remediation for the Cozystack management cluster`
- **Author(s):** `@lexfrei`
- **Date:** `2026-10-14`
- **Status:** Review

## Overview

When a node of the Cozystack cluster loses power or hangs, its pods stay `Terminating`. KubeVirt does not fail a VMI while its virt-launcher pod still looks alive, so a VM with `runStrategy: Always` often does not start anywhere else until someone deletes the pod or the Node. A VM whose disks are all on the `replicated` StorageClass may already be released by the Piraeus HA Controller that Cozystack ships; that is the controller's documented behaviour, not verified end to end on Cozystack. For a VM that mixes a replicated disk with other volumes, the controller's volume rules decide. A hoster wants a VM on a dead node to start again on another node by itself, without a person first checking that the machine is really off.

The hard part is not restarting the workload. It is proving that the old copy is gone, so that two copies of one VM never run at once. This proposal adds an optional `node-remediation` system package built on the Medik8s operators. NodeHealthCheck notices a node that stays unready. A fence agent powers the node off through its BMC, and only after the BMC confirms the action does the taint `node.kubernetes.io/out-of-service` release the node's pods and volume attachments. Without a BMC, a hoster leaves the package off and keeps the manual procedure, which the proposal documents. Watchdog self-fencing is analysed but left for a later proposal, because it cannot yet give the same guarantee.

The proof needs a BMC that answers. That covers a hung OS, a kernel panic, a failed data network, and a host that lost its main power while the BMC stays up on standby power, as with a failed board power rail. A node that lost mains power usually takes its BMC down with it; the fence then fails, this package releases nothing, and the manual procedure applies.

## Scope and related proposals

- [GPUDevice](../gpudevice/README.md) moves a dead node's attached devices to `Lost` after its grace period and rebinds them once KubeVirt fails the VMIs. When the VMIs fail before that, it can move the devices as the next VMIs are created. Fencing is what makes KubeVirt fail them without manual action.
- [GPU virtual machines](../vgpu-vms/README.md) states that a GPU VM on a failed node often does not come back by itself and points to this proposal for fencing.
- The GPUDevice and GPU virtual machines proposals are proposed in parallel. This proposal lands together with them or after them; until then the two links above do not resolve.
- In scope: nodes of the Cozystack cluster itself, both Talos and generic Linux (k3s, kubeadm).
- Out of scope: worker nodes of tenant Kubernetes clusters, which have their own Cluster API MachineHealthCheck (section 5), and workers on other substrates, such as Proxmox nodes.

## Decisions

## Context

### What exists

- Kubernetes marks a node `NotReady` when kubelet stops reporting. Its pods are deleted with a grace period after the default 300s tolerations, but kubelet never confirms the deletion, so they stay `Terminating`.
- [Non-graceful node shutdown](https://kubernetes.io/blog/2023/08/16/kubernetes-1-28-non-graceful-node-shutdown-ga/) is GA since Kubernetes 1.28. On a `NotReady` node with `node.kubernetes.io/out-of-service:NoExecute`, the pod garbage collector force-deletes pods that do not tolerate the taint, and the attach/detach controller detaches their volumes at once instead of after its 6-minute timeout. Kubernetes leaves the proof that the node is really off to whoever sets the taint.
- KubeVirt v1.9.0 fails a running VMI as soon as its virt-launcher pod is gone. A separate [node controller](https://kubevirt.io/user-guide/cluster_admin/unresponsive_nodes/) path fails VMIs whose pod still exists but counts as dead, after a five-minute virt-handler heartbeat timeout. `runStrategy: Always` then creates a new VMI on a healthy node.
- Cozystack ships the Piraeus HA Controller v1.3.1, enabled because `LinstorCluster` does not set `highAvailabilityController`. When a DRBD volume becomes promotable on a healthy node, which DRBD quorum guarantees only if no other node still uses it, the controller taints the failed node `drbd.linbit.com/lost-quorum`, evicts the pods using the volume (a force-delete on a node that is not `Ready`), and then deletes the volume's `VolumeAttachment` there unless an eviction failed. It skips the eviction, but not the taint or the detach, for pods without an owner, pods it is told to ignore, and pods that also use a volume it considers unsafe, such as a non-LINSTOR PVC; ephemeral volumes count as safe. It is designed for volumes with quorum, as in the `replicated` StorageClass of the [installation guide](https://cozystack.io/docs/v1.6/install/cozystack/platform/); the skip of volumes without a quorum policy arrived only in v1.3.2. The `local` class has no DRBD layer.
- `monitoring-agents` already alerts on `KubeNodeNotReady` and `KubeNodeUnreachable`.
- The optional `local-ccm` package has a node-lifecycle-controller that deletes a `Node` that stays `NotReady` and does not answer ping. By default it acts only on nodes the cluster autoscaler is removing; with a `nodeSelector` or with `watchAutoscalerTaint: false` it acts on every matching worker, which releases pods without proof that the node is off. The documentation says not to combine it with this package on nodes that are not autoscaled.

### The problem

1. A VM with no replicated disk, for example one booted from a container disk, does not restart elsewhere while the failed node is down. The same holds for every pod whose owner waits for the old pod to go away: StatefulSet replicas, and pods that need a volume still attached to the dead node. Pods of a ReplicaSet without such a volume are not in this group: after the 300s toleration the ReplicaSet creates a replacement elsewhere.
2. A VM whose disks are all on `replicated` may already be released by the HA Controller, which Cozystack has not verified end to end. Even then the proof covers the data of each volume, not the node. If the node is only cut off from the network, the old VM keeps running with suspended or failing writes, and its network identity, such as its IP and MAC, is alive twice once the new copy starts.
3. The manual procedure is unwritten. An operator who force-deletes pods on a node that is only partitioned can run two copies of a workload on non-DRBD storage, and lets the attach/detach controller detach volumes underneath workloads that may still run there.

## Goals

- A VM with `runStrategy: Always` or `RerunOnFailure` on a node that died starts again on a healthy node without manual action, once the hoster has enabled the package, as long as the node's BMC still answers and, for a VM with GPUDevice references, GPUDevice can place its devices on a `Ready` node with room when the VM restarts. A `Stay` device on a cordoned node is an exception (section 3).
- This package releases nothing from a node before the BMC confirms a power action on it. Without confirmation, the alert stays and the manual procedure applies. The HA Controller keeps its existing per-volume behaviour (section 4).
- A network partition, or a failure of several nodes at once, does not trigger a fencing storm beyond the `minHealthy` bounds of section 2.
- A fenced node stays powered off until a person powers it on; once it is `Ready` again, it rejoins without manual untainting.
- The manual procedure for clusters without automatic fencing is documented and uses the same taint.

### Non-goals

- Keeping a VM running through a node failure. The VM restarts from a fresh boot; live migration needs a running source.
- Recovering data that lives only on the dead node. A VM or pod with a volume on `local` stays down until its node returns.
- Fencing nodes of tenant Kubernetes clusters.
- Detecting faults other than an unready node, such as a degraded disk or a failed NIC bond.
- Cloud providers with their own instance API. Fence agents cover some clouds, but only bare metal with a BMC is verified.

## Design

### 1. Components

A new optional system package, `node-remediation`, ships two [Medik8s](https://github.com/medik8s) operators, both Apache-2.0:

- **NodeHealthCheck (NHC)** watches node conditions and, when a node stays unhealthy, creates a remediation CR from a template. It does not fence by itself.
- **Fence Agents Remediation (FAR)** runs a fence agent against the node's BMC and, with `remediationStrategy: OutOfServiceTaint`, sets the out-of-service taint after the fence action succeeds. It removes the taint when its CR is deleted, which NHC does once the node is healthy again.

Upstream installs them through OLM, which Cozystack does not ship, so the package vendors their manifests from each release tag and pins the images, as other operators under `packages/system` are vendored. The webhooks get their certificates from cert-manager. Each operator runs two replicas spread across nodes with leader election.

The package installs in three releases into `cozy-node-remediation`, ordered with Flux `dependsOn` the way `cert-manager-crds` is split from `cert-manager`: the CRDs (kept on removal), then the operators and their webhooks, then the NHC and template objects. The FAR templates, the remediation CRs and the BMC credentials Secrets live in a second namespace, `cozy-node-remediation-bmc`, which release 1 creates without the `cozystack.io/system` label, so that it holds nothing else.

### 2. Hoster configuration

The package is disabled by default. A hoster enables it through `enabledPackages` and configures it in platform values, which the platform passes to the releases as Package component values:

```yaml
nodeRemediation:
  unhealthyAfter: 300s        # Ready=False or Unknown for this long
  workerMinHealthy: 51%       # workers: no remediation below this share
  stormCooldown: 5m           # NHC stormCooldownDuration
  paused: false               # true: start no new remediation; disable order only
  bmc:
    agent: fence_redfish      # fence_redfish | fence_ipmilan
    credentialsSecretName: bmc-credentials  # required, in cozy-node-remediation-bmc
    parameters:                # connection options for all nodes, allow-listed
      --ssl-insecure: ""
    nodes:
      node1:
        address: 192.0.2.11
      node2:
        address: 192.0.2.12
        credentialsSecretName: bmc-node2    # overrides the shared one
        parameters:            # per-node fence agent parameters
          --systems-uri: /redfish/v1/Systems/1
```

The rules below exist because FAR passes Secret keys and parameters to the fence agent almost unchecked, and one wrong key can release a running node:

- The action is always `off`. The chart sets it, and no map accepts an action key. Kubernetes requires that a node is already shut down, not in the middle of restarting, before the out-of-service taint is set, and FAR sets the taint after the agent exits. With `reboot` the agent exits after powering the node back on, so the taint would land on a booting node whose kubelet could restart a pod just as the taint force-deletes it and detaches its volumes.
- The agent is `fence_redfish` or `fence_ipmilan`. FAR accepts any binary in its image, including `fence_ifmib`, which shuts a switch port; with the out-of-service taint, a "successful" fence of that kind would release a running node.
- Both `parameters` maps are allow-lists, with keys matched exactly, because fence agents accept abbreviated and short forms of an option and FAR silently falls back to a shared value for a node without its own. The shared map takes only connection options of the chosen agent that neither identify a machine nor select an action (`--ssl-insecure` for Redfish, `--lanplus` for IPMI, `--power-wait` and `--power-timeout` for both); the per-node map also takes, for `fence_redfish`, `--systems-uri`, which is required on a Redfish manager with more than one system, since `fence_redfish` otherwise takes the last one it lists and reports success. The address comes only from `address`.
- Credentials Secrets hold only `--username` and `--password`. A ValidatingAdmissionPolicy on Secrets in `cozy-node-remediation-bmc` refuses any other key. Without it, a bare `--help` would make the agent exit 0 with no power action, which FAR counts as success, and an `--ip` in the shared Secret would aim every node without its own entry at one machine. The policy ships in the first release and checks only Secrets written after it exists, so the hoster creates the Secrets after that release is Ready.
- The key under `nodes` is the Node name, which FAR uses to look up per-node parameters. A selected node without an entry still gets a remediation CR, but FAR has no address for it, so its fence fails and this package releases nothing.

The chart refuses at render time what it can see, such as another agent, keys outside the allow-lists, or an empty `nodes` or `credentialsSecretName`. The order in which the hoster creates and deletes Secrets matters too: FAR refuses a template that names a missing Secret, and it re-validates every referenced Secret on each update of an open remediation CR. The documentation gives that order.

The package renders two NHC objects, because a typical Cozystack cluster runs workloads on its three control-plane nodes and a worker-only selector would fence nothing there. Both selectors are fixed, because NHC's webhook refuses to change a selector or delete an NHC while a remediation is open:

- Control-plane nodes are selected by the `node-role.kubernetes.io/control-plane` label alone, so a node that joins later is covered without a change to values. `minHealthy` is a strict majority, rendered as `51%`, which NHC rounds up (2 of 3, 3 of 4, 3 of 5). NHC itself never runs more than one control-plane remediation at a time, and since a fenced node stays off, "at a time" lasts until a person powers it on, or deletes its `Node` and removes the finalizer of its FAR CR, which FAR v0.8.0 leaves on a CR whose `Node` is gone. A control-plane CR whose fence failed blocks the same way until it is deleted; while its node is still unhealthy, NHC then creates a new CR for that node. Fencing one member of three leaves two etcd members, which keep quorum whether or not the fenced member still voted. On three nodes, when two are unhealthy, nothing is fenced. NHC counts over the nodes its selector matches, not over etcd members, which is why the selector cannot leave any control-plane node out. NHC's etcd-aware gate is OpenShift-specific and is not used.
- Worker nodes are selected by the absence of the control-plane role and use `workerMinHealthy`. A hoster with a small pool lowers it; at `51%`, a pool of two workers cannot remediate a single failure.

NHC counts nodes still inside `unhealthyAfter` and nodes being remediated as unhealthy, so failures that follow one another add up against `minHealthy`. The storm cooldown covers the end of such a storm: when a rack gets power back, its nodes boot one after another, and without a pause the first ones back would let NHC fence those still booting.

A pause, set with `paused: true`, adds a pause request to both NHCs and stops new remediations. A pause also stops NHC's cleanup, so a node fenced before the pause keeps FAR's taints until the pause ends. The package therefore uses it only in the disable order (Upgrade and rollback), after every remediation has closed.

### 3. What happens after a node fails

1. kubelet stops reporting; the node controller marks the node `NotReady` after its grace period.
2. If the node has DRBD volumes in use, the HA Controller may already have released those pods, as today.
3. After `unhealthyAfter`, NHC checks `minHealthy` and creates a remediation CR for the node.
4. FAR powers the node off through its BMC.
5. FAR sets `node.kubernetes.io/out-of-service=nodeshutdown:NoExecute`; Kubernetes force-deletes the node's remaining pods and detaches their volumes.
6. KubeVirt fails each VMI as soon as its pod is gone, and `runStrategy` decides what comes next. The next VMI of a VM with GPUDevice references also needs its devices, and GPUDevice's rules for a dead node decide where and when it gets them; section 5 covers pool workers. When the fence comes before GPUDevice's grace period for a `NotReady` node, GPUDevice gathers the devices the VM names in `device` entries onto another node with room, as GPUDevice's gather rule allows, and the VM starts there. A device of a `Stay` class on a cordoned dead node can keep the new VMI on that node until the grace period ends, after which a `RerunOnFailure` VM can stay stopped; GPUDevice's section 3 has the exact conditions. Template devices, devices already `Lost` and the case of no node with room follow GPUDevice's rules.
7. When a person powers the node on and it is `Ready` again, NHC deletes the remediation CR and FAR removes its taints.

With defaults, a VM runs elsewhere roughly six to eight minutes after the failure when the BMC answers on the first try. A VM with GPUDevice references can take longer in the cases step 6 leaves to GPUDevice.

### 4. Proof that a node is dead

The design question is which signal is trusted to say that the old node runs nothing.

**BMC power fencing (this proposal).** The BMC is an independent channel: it answers even when the node's OS is hung or its data network is down, and a successful `off` states that the old processes are gone. It covers every workload on the node and ends the duplicate network identity. It fails safe: if the BMC is unreachable or refuses the credentials, this package releases nothing. Its cost is that the cluster must reach every BMC and hold credentials for it (Security).

**Watchdog self-fencing (not in this proposal).** Medik8s Self Node Remediation (SNR) lets an isolated node reset itself through a watchdog; the peers wait a safe time and then set the taint. It needs no BMC, but its proof is elapsed time, and SNR v0.13.0 has gaps that block it here:

- A watchdog held open by another process still marks the node as able to reboot; the agent then reboots through sysrq from the kernel that may be hung, even with software reboot turned off, and the peers release the node after the safe time.
- An agent that reaches neither the API server nor any peer resets its node whatever `minHealthy` says, so a data network failure resets every node at once.
- With no watchdog device, SNR loads `softdog`, which runs in the same kernel.
- On Talos with a `WatchdogTimerConfig` document (since v1.7), Talos itself feeds the device.

A later proposal can add it as a second remediator once these are solved.

**DRBD quorum (existing, per volume).** The HA Controller's proof is that DRBD lets a volume be promoted elsewhere. It is strong for that volume's data and already active, but says nothing about the rest of the node and has a known false positive (Open questions). Both run side by side, and whichever releases a pod first wins.

**Manual confirmation.** A person checks the console or the power state and sets the taint. It needs no package and is the only option without a BMC.

Kubernetes `NotReady` alone is not proof: a node that lost its network to the control plane looks the same as a node that lost power.

### 5. Interaction with tenant clusters

A tenant worker from a `KubernetesNodes` pool on the KubeVirt provider is a VM on the management cluster. When its host dies, fencing restarts the VM elsewhere, and its system disk survives when it is on `replicated`. Independently, the tenant's MachineHealthCheck replaces a worker whose node stays `Ready=Unknown` for `readyUnknownTimeout`, with a fresh disk. With default settings MHC fires first in a pool of more than one worker, which discards worker-local state such as local-path volumes; a tenant who keeps such state raises `readyUnknownTimeout` above `unhealthyAfter` plus the fence and boot time. For a Kairos GPU worker from a `deviceSelector` pool, both the VM that fencing restarts and an early MHC replacement get a GPU only from a spare device of the pool on a healthy node: the dead worker's device serves no new worker before GPUDevice's own grace period for a `NotReady` node has passed and the old VMI has failed or been removed. A worker from a `deviceTemplate` pool gets a new device instead, as GPUDevice describes. Medik8s is not installed inside tenant clusters.

### 6. Manual procedure

Without the package, and as the fallback when automatic fencing cannot act: confirm the node is off through the BMC, the hypervisor or on site (a node that only lost its network is not off), set `node.kubernetes.io/out-of-service=nodeshutdown:NoExecute` on it, and remove the taint once it is `Ready` again, or delete the `Node` if it will not come back. A FAR CR whose `Node` was deleted then needs its finalizer removed by hand.

### 7. Excluded nodes and maintenance

A node labelled `remediation.medik8s.io/exclude-from-remediation=true`, NHC's own label, is never remediated. NHC checks it after selection, so an excluded node stays counted toward `minHealthy` and its cleanup keeps running, and setting the label changes no NHC object. A hoster uses it for nodes whose fencing would be worse than waiting, labels every control-plane node to turn control-plane fencing off, and labels a node before planned work: NHC on non-OpenShift clusters does not know about maintenance and remediates cordoned nodes, so a node that stays down longer than `unhealthyAfter` during an upgrade or a firmware update would otherwise be powered off in the middle of it.

## User-facing changes

- A new optional system package `node-remediation` and a `nodeRemediation` block in platform values.
- New CRDs: `NodeHealthCheck` and `FenceAgentsRemediation(Template)`.
- Events, conditions and taints on remediated nodes from NHC and FAR.
- Documentation of the manual procedure, the maintenance procedure, the credentials order and the StorageClass options the HA Controller needs.
- Tenants see nothing new, except that their VMs come back after a host failure.

## Upgrade and rollback compatibility

- Existing clusters keep today's behaviour until the hoster enables the package. Enabling it changes nothing on healthy nodes.
- An optional Cozystack package is rendered with `helm.sh/resource-policy: keep`, so removing it from `enabledPackages` uninstalls nothing, and deleting its Package CR removes the three releases with no `dependsOn` order. If the operators went first while a remediation is open, FAR CRs would stay on their finalizer and leave FAR's taints on a node. The documented disable order prevents this: wait until no remediation is open, pause both NHCs and check again, remove the package from `enabledPackages`, then delete the Package CR.
- The CRDs and NHC's `medik8s-leases` namespace stay after removal. `cozy-node-remediation-bmc` goes with release 1, and the BMC credentials with it. Before a Cozystack rollback to a release without the package, the hoster disables it first. The HA Controller's `lost-quorum` taint is not this package's.

## Security

- FAR looks Secrets up only in the namespace of its remediation CRs but reads them through a cluster-wide cache, so it needs cluster-wide Secret read; upstream gets that from OLM, which turns FAR's namespaced permissions into a ClusterRole. The package grants the same ClusterRole, so FAR can read every Secret, including those in `cozy-system`, as an upstream install can. Scoping the cache would need an upstream change (Open questions).
- A BMC account that can power a node off is a powerful credential. The documentation recommends an account limited to power control and a BMC network reachable only from the cluster nodes, which also matters with `--ssl-insecure`: then a successful `off` proves the node is dead only if nobody else can answer on the BMC's address.
- FAR runs non-root without privilege escalation, but its ClusterRole updates and deletes `Node` objects, deletes pods and `VolumeAttachment`s cluster-wide and may exec into pods. It runs only on the management cluster and is not reachable by tenants.
- Tenants cannot create remediation CRs or set the out-of-service taint; tenant roles have no access to `Node` objects.
- A wrong address under `nodes` fences the wrong machine, and the chart cannot tell which machine sits behind an address. The documentation requires checking each mapping, with the agent's `status` action and the identity of the system at the URI the agent will use, before enabling the package and whenever a node is added or replaced. This is a procedure, not a guard (Open questions).

## Failure and edge cases

- BMC unreachable or credentials wrong → FAR retries for between about 20 seconds and five and a half minutes, then marks the fence failed. A failed fence is final for that CR, and this package releases nothing, while the HA Controller may still release pods with replicated volumes; the alert stays. After fixing the BMC access the hoster deletes the failed CR, and NHC creates a new one while the node is still unhealthy.
- Network partition → nodes on the minority side are unhealthy from the majority's view; NHC remediates them only within `minHealthy`, and this package powers each one off before it releases its pods; the HA Controller may still release pods with replicated volumes on quorum alone.
- A node reports `Ready=False` while its workloads keep running, for example with a broken runtime or CNI → it is powered off after `unhealthyAfter`. Nothing runs twice, but the workloads restart elsewhere.
- A node, or a rack, loses power together with its BMC → the fence fails and this package releases nothing; the HA Controller may still release pods with replicated volumes, and the manual procedure covers the rest.
- Many nodes become unhealthy while their BMCs answer → each NHC checks `minHealthy` over its own nodes; one below its threshold fences nothing, and the control-plane NHC fences at most one node at a time.
- A credentials Secret is deleted while a remediation CR created before the change is open → FAR cannot remove that CR's finalizer, the CR stays `Terminating`, and a stuck control-plane CR blocks other control-plane remediations until the Secret is restored.
- Node comes back during fencing → NHC deletes the CR, which cancels the agent; an `off` the BMC already accepted is not undone.
- A node joins the cluster later → its NHC selects it at once; until it is added under `nodes`, its fence fails and this package releases nothing.
- A control-plane node is `Ready` but its etcd member is down, and another control-plane node fails → the failed one is fenced and etcd is left with one working member of three. Node conditions do not show etcd health (Open questions).
- VM with a disk on `local` on the dead node → released, but it cannot start elsewhere until the node returns.

## Testing

- Chart unit tests for every render-time refusal of section 2, the fixed NHC selectors, the `off` action, the pause request, and the values path through Package component values.
- e2e on virtual nodes with a Redfish emulator such as sushy-tools: a VM with a `replicated` disk and a VM with a container disk both restart elsewhere; with the emulator unreachable, the container-disk VM is not released; after a retry the node is fenced; the taint is removed after power-on, and DaemonSets such as the CNI and the LINSTOR satellite come back while the taints are still on. A DaemonSet that fails this check gets tolerations for both taints in its Cozystack chart before the package ships, since otherwise the node never becomes `Ready` and the taint stays.
- e2e for the operations the documentation describes: the credentials order and the admission policy, the disable order with an open remediation, the maintenance label, and `minHealthy` on three control-plane nodes that run workloads (one failure fenced, two not).
- Manual verification on bare metal on the shipped HA Controller v1.3.1: what it does with a VM whose disks are all `replicated`, with one that mixes replicated and ephemeral volumes, and with a `local`-only pod. This proposal assumes the first case from upstream documentation.

## Rollout

1. The package, with documentation of the manual procedure, the maintenance procedure, the credentials order and the HA Controller's StorageClass requirements.
2. A separate proposal for watchdog self-fencing, once the gaps in section 4 are solved.

## Open questions

- etcd health: should the control-plane NHC count a `Ready` node whose etcd member is down as unhealthy, through a node condition set from the local member's status?
- Should NHC watch only `Ready=Unknown`, leaving `Ready=False` to the alerts, so that a node whose runtime or CNI fails is not powered off while its workloads run?
- Should Cozystack propose upstream that FAR scope its Secret cache to its own namespace?
- Should the package also accept PDU fence agents, such as `fence_apc_snmp` or `fence_eaton_snmp`, which can prove a chassis without mains power is off where its BMC cannot answer?
- Retry after a failed fence: should the package expose FAR's `retrycount` (with a minimum of 1: at 0 FAR v0.8.0 records success without running the agent), `retryinterval` and `timeout`, or retry through NHC's escalating remediations with a second FAR step? NHC v0.12.0 seems to track started remediations by kind only, which needs checking first.
- BMC mapping: should Cozystack add a controller or webhook that checks each node's BMC address against the node, for example by its system UUID, instead of the documented check?
- The HA Controller has an open upstream report of two healthy nodes evicting a workload from each other when the link between them goes StandAlone ([piraeus-ha-controller#154](https://github.com/piraeusdatastore/piraeus-ha-controller/issues/154)). Should Cozystack keep the controller as the fast path, or rely on node fencing alone?

## Alternatives considered

- **Release pods when a node is `NotReady` for some time, without fencing.** Rejected: a partitioned node is `NotReady` too, and releasing its pods runs two copies of every non-DRBD workload.
- **Rely on the HA Controller only.** Rejected as the only mechanism: it covers pods with DRBD volumes with quorum and does not stop the old VM's network identity.
- **Cluster API MachineHealthCheck for management nodes.** Rejected: Cozystack's own nodes are not Cluster API machines, and deleting a bare-metal machine is not a fencing action.
- **A Cozystack-specific controller that talks Redfish and sets the taint.** Rejected: it would reimplement NHC and FAR, including their safety checks for partitions and minimum healthy share, without their field history.
- **Delete the `Node` object instead of tainting it.** Rejected as the automatic path: deleting the `Node` drops the labels and taints set through the API, and the node has to register again. The taint is reversible and is the mechanism Kubernetes designed for this.
