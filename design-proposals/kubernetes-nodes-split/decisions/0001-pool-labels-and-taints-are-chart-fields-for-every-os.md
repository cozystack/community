# 0001. Pool labels and taints are `kubernetes-nodes` chart fields for every worker OS, not a Talos `userMachineConfig` layer

- **Number:** `0001`
- **Date:** `2026-10-14`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4927`](https://github.com/cozystack/cozystack/pull/4927)
- **Implemented in:** [`cozystack/cozystack#4927`](https://github.com/cozystack/cozystack/pull/4927), open at the time this record landed; this header line tracks its state and is updated when it merges

## Context

The proposal described a two-layer Talos machine config per pool: a chart-managed system layer and a `userMachineConfig` user layer in `values.yaml` for "extra kubelet args, extra labels/taints, registry mirror overrides, Talos image-factory schematic" ([Talos machineconfig: template + user overlay](../README.md#talos-machineconfig-template--user-overlay)). The split shipped without the user layer. Registry mirrors and the image-factory schematic became their own chart fields (`talos.registryMirrors`, `osImage`), and `userMachineConfig` was never added. The only labels a pool carried were its `roles`, as `node-role.kubernetes.io/<role>`, and `gpu: "on"` on a GPU pool. It had no taints, so GPU workers took ordinary workloads too.

A pool was also about to stop being always Talos. `kubernetes-nodes` was gaining `os: kairos` ([`cozystack/cozystack#4911`](https://github.com/cozystack/cozystack/pull/4911), which this change is built on), whose workers join with `kubeadm join` through the Kairos Cluster API bootstrap provider and have no Talos machine config at all.

Two mechanisms put labels on a Node, with different limits. Cluster API syncs labels from a Machine to its Node only for `node-role.kubernetes.io` and the `node-restriction.kubernetes.io` and `node.cluster.x-k8s.io` domains ([`util/labels.GetManagedLabels`](https://github.com/kubernetes-sigs/cluster-api/blob/v1.13.7/util/labels/helpers.go) in v1.13.7). Everything else goes through the worker's bootstrap config. On Kairos that is kubelet `--node-labels`, which admits only a restricted set of `kubernetes.io` and `k8s.io` keys ([`IsKubeletLabel`](https://github.com/kubernetes/kubernetes/blob/v1.35.6/staging/src/k8s.io/kubelet/pkg/apis/well_known_labels.go)). On Talos it is `machine.nodeLabels`, which machined writes to the Node with the kubelet's credentials. Cluster API v1.13 can also manage Machine taints, but behind the alpha `MachineTaintPropagation` feature gate, which is off by default ([`feature/feature.go`](https://github.com/kubernetes-sigs/cluster-api/blob/v1.13.7/feature/feature.go)).

## Decision

`KubernetesNodes` gains two chart fields that apply to every worker OS: `labels`, a string map, and `taints`, a list of taints with a key, an optional value and an effect. Labels in the domains Cluster API syncs go on the MachineDeployment template and reach the Nodes in place. Every other label and every taint goes through the pool's bootstrap config: `machine.nodeLabels` and the kubelet's `registerWithTaints` on Talos, kubeadm's `node-labels` and `nodeRegistration.taints` on Kairos. The chart refuses, at render time, label keys and values Kubernetes would reject, labels that describe the node itself or that another writer owns, and taints in namespaces reserved for Kubernetes and Cluster API. The rules live in the `kubernetes-nodes.labelsAndTaints` helper of `packages/apps/kubernetes-nodes/templates/_helpers.tpl` and are pinned by `packages/apps/kubernetes-nodes/tests/labels_taints_test.yaml`.

## Why not the alternatives

All three were weighed in the implementation pull request ([`cozystack/cozystack#4927`](https://github.com/cozystack/cozystack/pull/4927)).

- **A Talos `userMachineConfig` overlay, as the proposal described** ([proposal section](../README.md#talos-machineconfig-template--user-overlay)). It only exists for Talos, so a Kairos pool would need a second, different mechanism for the same thing. It also hands the tenant raw machine-config YAML, which the chart then has to merge and keep from overriding cluster-critical fields. Two typed fields do not have that problem.
- **Machine `spec.taints` with Cluster API taint propagation.** It would let taints change without rolling the pool, but in Cluster API v1.13 it sits behind an alpha feature gate that is off by default ([`feature/feature.go`](https://github.com/kubernetes-sigs/cluster-api/blob/v1.13.7/feature/feature.go)). Turning it on is a platform-wide change to the core provider, and an alpha API can still change shape. This lost on maturity, not on design, so it is the first thing to revisit.
- **All labels through the MachineDeployment template, with Cluster API's `--additional-sync-machine-labels`** ([metadata propagation](https://github.com/kubernetes-sigs/cluster-api/blob/v1.13.7/docs/book/src/reference/api/metadata-propagation.md)). It would make every label change in place. The flag takes regexes and applies to every tenant cluster the core provider manages. Tenant label keys are free-form, so a regex that covers them is in effect `.*` for every tenant, while a narrower prefix would force tenants into a Cozystack-owned prefix. This lost on a judgement call: registering the rest through the bootstrap config keeps the platform configuration unchanged.

## Consequences

- One field set works for Talos and Kairos pools, and a pool without labels or taints renders exactly as before.
- Labels outside the Cluster API domains and all taints are part of the bootstrap config, so changing them rolls the pool. The field descriptions say which labels change in place.
- The chart applies one set of per-key label rules to both OSes, including where Talos alone would accept more. The exact set of refused keys is in the helper and its test, not restated here, because it tracks the Kubernetes, Talos and cloud-controller versions the platform ships.
- The proposal's `userMachineConfig` user layer is not planned. Further per-pool settings follow the same pattern: a typed chart field that each OS path renders.
- The user layer also listed extra kubelet arguments. This record does not decide them, and the chart still has no general way to pass them.

## Revisit if

Cluster API's `MachineTaintPropagation` reaches beta or is enabled on the platform for another reason. Taints could then move to Machine `spec.taints` and change without a rollout.
