# 0002. A Cilium deny policy, not a client label, keeps other pods off a cluster's etcd

- **Number:** `0002`
- **Date:** `2026-10-08`
- **Status:** Accepted
- **Deciders:** `@lllamnyp, @lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/cozystack#4817`](https://github.com/cozystack/cozystack/pull/4817), argued on [`cozystack/community#25`](https://github.com/cozystack/community/pull/25#pullrequestreview-5491419490)
- **Implemented in:** [`cozystack/cozystack#4817`](https://github.com/cozystack/cozystack/pull/4817)

## Context

The proposal as reviewed said the `policy.cozystack.io/allow-to-etcd` pod label would retarget to the cluster's own etcd, so the network policy would stay tight rather than subtree-wide.

@lexfrei's [review](https://github.com/cozystack/community/pull/25#pullrequestreview-5491419490) showed that the label reads nowhere a per-cluster etcd could use: only the tenant's `<tenant>-ingress-etcd` policy reads it, that policy renders only while the tenant's `etcd` is set, and Phase 4 removes it. The same tenant chart's `allow-internal-communication` admits every pod in the namespace to every other, the tenant also admits ancestor tenants' namespaces and the `world` entity, and Cilium allow rules only add. A per-cluster etcd in the tenant namespace was therefore reachable from every pod there, including other clusters' control planes.

## Decision

The chart renders a `CiliumNetworkPolicy` named after the etcd, selecting its members, with `ingressDeny` on 2379 and 2380 against three sources: tenant-namespace pods that are neither members nor labelled `policy.cozystack.io/allow-to-etcd-cluster: <etcd name>`; pods in every other namespace except `kube-system` and those labelled `cozystack.io/system: "true"`; and `world`. The comment above the policy in `packages/apps/kubernetes/templates/etcd.yaml` carries the mechanism; `packages/apps/kubernetes/tests/etcd_test.yaml` pins its shape, and the `kubernetes-latest` e2e suite probes it from an allowed and two refused pods.

## Why not the alternatives

- **Retarget `policy.cozystack.io/allow-to-etcd`**, the proposal's own design. Nothing reads that label for a per-cluster etcd, and no allow rule can narrow what the tenant's policies already admit ([lexfrei, point 4](https://github.com/cozystack/community/pull/25#pullrequestreview-5491419490), [inline](https://github.com/cozystack/community/pull/25#discussion_r4248519934)).
- **Deny pod sources only.** `fromEndpoints` matches pods, and the tenant's `allow-external-communication` admits `world` to every endpoint, so off-cluster sources such as Proxmox workers would still reach the ports ([themoriarti, finding 5](https://github.com/cozystack/cozystack/pull/4817#discussion_r4266874152)).
- **Admit the platform by pod label.** Neither Kamaji's controller nor its migrate Job carries the client label. The `cozystack.io/system` namespace label, which the package reconciler sets on every non-tenant namespace it creates, covers them without one ([CodeRabbit, withdrawn after checking](https://github.com/cozystack/cozystack/pull/4817#discussion_r4614134000)).

## Consequences

- The fence exists only where Cilium serves `cilium.io/v2/CiliumNetworkPolicy`; elsewhere the etcd is protected by its client certificates alone.
- The client label is honoured from any pod in the cluster's namespace, and anyone who can create pods there can set it. Pods in other tenant namespaces are refused whatever labels they carry.
- The node's own `host` and `remote-node` traffic is not denied, and metrics on 2381 stay open.
- A cluster on a shared etcd is unaffected and keeps the subtree-wide `allow-to-etcd` path until Phase 4.

## Revisit if

Tenant namespaces move to deny-by-default policies, or the platform's network policy moves off Cilium.
