# 0005. The taint rule skips exactly the three taints that mirror a cordon or readiness

- **Number:** `0005`
- **Date:** `2026-10-20`
- **Status:** Accepted
- **Deciders:** `@lexfrei`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#99`](https://github.com/cozystack/community/pull/99)
- **Implemented in:** [`cozystack/cozystack#4998`](https://github.com/cozystack/cozystack/pull/4998)

## Context

Section 2 keeps binds, moves, rebinds and gathers off nodes with a `NoSchedule` or `NoExecute` taint that virt-launcher pods do not tolerate, and leaves out "the `node.kubernetes.io/*` taints Kubernetes sets itself for a cordon or a node that is not `Ready`", because the cordon and readiness rules already cover those nodes. Read as a prefix, that sentence would also skip the other well-known taints Kubernetes defines under `node.kubernetes.io/`: `memory-pressure`, `disk-pressure`, `pid-pressure`, `network-unavailable` and `out-of-service`. None of them mirrors a cordon or readiness, and a node carrying one of them as `NoSchedule` or `NoExecute` would still be bound to, though the scheduler keeps a virt-launcher pod that does not tolerate it off the node.

## Decision

The rule skips exactly `node.kubernetes.io/unschedulable`, `node.kubernetes.io/not-ready` and `node.kubernetes.io/unreachable`. Every other `NoSchedule` or `NoExecute` taint that virt-launcher pods do not tolerate counts, under `node.kubernetes.io/` or not. The rule and its cases live in `internal/gpu/model/node.go` and `node_test.go`.

## Why not the alternatives

- **Skip the whole `node.kubernetes.io/` prefix.** It binds claims onto nodes that the scheduler then refuses, and keeps their units in `free`, which is the failure the taint rule exists to prevent.
- **Count the three as well.** Kubernetes sets `node.kubernetes.io/unschedulable` on every cordoned node, so a cordoned node would also read as tainted. A taint keeps a bound claim where it is, so under `Move` a cordon would never move a claim, and `onCordon` would stop meaning anything.

## Consequences

- A node under memory, disk or PID pressure, one whose network is unavailable, and one tainted `out-of-service` get no binds, moves or gathers while the taint holds and virt-launcher pods do not tolerate it, and their units leave `free` and `maxFreePerNode`. Outside a gather at VMI creation, a claim already bound there stays, as on any tainted node.
- A pressure taint comes and goes with the node's condition, so `free` on a busy class can drop and return without any claim moving.

## Revisit if

Kubernetes adds a taint of its own that mirrors a cordon or readiness, or Cozystack's virt-launcher pods start tolerating taints by default.
