# Targeted default-deny for SecurityGroup members

- **Title:** `Targeted default-deny for SecurityGroup members`
- **Author(s):** `@mattia-eleuteri`

## Overview

A SecurityGroup (`sdn.cozystack.io/v1alpha1`) restricts nothing today. It projects to a CiliumNetworkPolicy that only adds allow rules, and every tenant namespace already allows all traffic through `allow-external-communication` and `allow-internal-communication`. The upstream design says so itself and defers a default-deny baseline to future work. This proposal makes membership restrictive without changing anything for workloads that are in no group: a pod that belongs to at least one SecurityGroup leaves the tenant's allow-all baseline, and only its groups, the platform's own flows and the application's own flows open it.

It takes three changes, measured together on a lab: the securitygroup-controller keeps one constant label, `sdn.cozystack.io/member`, on every member pod; the tenant chart's two allow-all policies stop selecting pods that carry it; and each managed-application chart renders a `<release>-platform` policy that keeps the application talking to itself and to the outside world, so that a group cannot break replication, cluster membership or backups. The third change must be in place before the second, because a replicated database that becomes a member without its platform policy can lose a committed write.

## Scope and related proposals

This proposal builds on the SecurityGroup design in cozystack/cozystack at `pkg/apis/sdn/DESIGN.md`, and implements a narrow part of its §8 future work: the default-deny baseline, limited to members, and the platform-traffic carve-outs for managed applications. It does not touch the SecurityGroup API, its projection or its validation.

Related issues and pull requests in cozystack/cozystack:

- cozystack/cozystack#5124: the node VMs of a Kubernetes cluster are admitted without `internal.cozystack.io/managed-by-cozystack`, so the controller never sees them and they never become members. A group attached to a Kubernetes cluster leaves its nodes open until it lands. It is a prerequisite for the Kubernetes part of this proposal, not part of it.
- cozystack/cozystack#5129: a group with attachments and no rules projects to a policy Cilium 1.19 marks invalid. Membership still works, and this proposal relies on membership only.
- cozystack/cozystack#5130: validation of port ranges and FQDNs. Independent of this proposal.
- cozystack/cozystack#5131: `toFQDNs` never works with Cilium chained on kube-ovn, which limits what a group can express once it restricts (see Non-goals).

## Decisions

## Context

All measurements below were taken on a lab running Cozystack v1.6.5, with Cilium 1.19.5 chained (`generic-veth`) on kube-ovn and `enable-policy=default`.

A SecurityGroup is projected 1:1 into a CiliumNetworkPolicy of the same name, whose `endpointSelector` is the group's membership label `securitygroup.sdn.cozystack.io/<name>`. The securitygroup-controller (`internal/securitygroupcontroller/reconciler.go`) stamps that label on the pods of the applications listed in `spec.attachments`, found by their lineage labels `apps.cozystack.io/application.{group,kind,name}`, and removes it on detach. Its pod cache only holds pods labelled `internal.cozystack.io/managed-by-cozystack=true`.

Every tenant namespace receives, from `packages/apps/tenant/templates/networkpolicy.yaml`:

- `allow-internal-communication` (CNP, `endpointSelector: {}`): ingress from and egress to every endpoint of the namespace.
- `allow-external-communication` (CNP, `endpointSelector: {}`): ingress from `world` and `cluster`, egress to `world`.
- `<tenant>-ingress` (CCNP): ingress from `kube-apiserver`, from namespaces labelled `cozystack.io/system`, from `kube-system` and from the parent tenants' namespaces. This is what carries monitoring scrapes, billing scrapes and operators' calls.
- `<tenant>-egress` (CCNP): egress to every namespace of the tenant and to a few parent-tenant services (vminsert, etcd, ingress).
- `allow-to-dns`, `allow-to-dashboard`, `allow-to-keycloak` and similar CNPs with `endpointSelector: {}`, each opening egress to one platform service.

Cilium allow rules are additive. As long as the first two policies select every pod, a SecurityGroup can only widen what is already fully open.

### The problem

A tenant who attaches a group with `ingress: [{fromApp: web, toPorts: 8080}]` to a database expects that only `web` reaches it, and only on that port. Measured in a real tenant namespace: any other pod of the namespace still reaches the member on every port, and a group with egress rules leaves DNS and the Internet open. The members are correctly labelled; the baseline simply allows everything. The API accepts the group, reports nothing, and protects nothing.

The same groups in a namespace without the baseline do restrict, for pods and for KubeVirt VMs alike: the allowed peer passes on its port, other ports and other peers are denied, with a `Policy denied` verdict in the Cilium monitor. The mechanism works; the baseline makes it inert.

Making the whole tenant default-deny, as §8 of the upstream design sketches, changes every workload of every tenant at once, and needs a membership admission webhook so that a fresh pod is not denied before the controller labels it. Neither is needed if only members leave the baseline.

## Goals

- A pod that is a member of at least one SecurityGroup accepts only the ingress its groups allow, plus the platform's ingress (API server, system namespaces, `kube-system`, parent tenants) and its own application's traffic.
- A member's egress outside the tenant is limited to what its groups allow, plus DNS, the platform services already opened by the baseline, and `world` for managed applications.
- A namespace, or a pod, that is in no group behaves exactly as today.
- A managed application (postgres, mariadb, mongodb, redis, rabbitmq, kubernetes) keeps replicating, electing, clustering and backing up while it is a member, whatever its groups say.
- Detaching the last group, or deleting it, returns the pod to the baseline.

### Non-goals

- A tenant-wide default-deny. Namespaces without groups keep the allow-all baseline.
- Restricting egress inside the tenant. `<tenant>-egress` allows every namespace of the tenant, and this proposal keeps it. A group therefore filters a member's egress to the outside only.
- FQDN-based egress. `toFQDNs` cannot work while Cilium is chained behind kube-ovn (cozystack/cozystack#5131): the DNS proxy never receives the redirected query, and a DNS L7 rule breaks the pod's resolution. Groups must use CIDRs.
- Buckets. The S3 endpoint of a bucket is served from the platform's object store outside the tenant's namespace; only the bucket's UI pod lives in the namespace. A group has no hold on bucket access.
- A membership admission webhook. An unlabelled pod is a non-member, so it is open as today, never wrongly closed (see Failure and edge cases).
- Any change to the SecurityGroup API, its projection or its validation.

## Design

### 1. A constant membership label, kept by the controller

The membership key differs per group (`securitygroup.sdn.cozystack.io/<name>`), and a Cilium selector cannot match a key prefix. The tenant chart needs a single key to exclude members, so the controller maintains one more label:

```yaml
metadata:
  labels:
    securitygroup.sdn.cozystack.io/db: ""
    sdn.cozystack.io/member: ""          # present iff at least one securitygroup.sdn.cozystack.io/* label is present
```

`addMembership` sets `sdn.cozystack.io/member` in the same patch as the group's key. `removeMembership` deletes it in the same patch as the group's key when no other `securitygroup.sdn.cozystack.io/*` key remains. Because the controller reconciles one group at a time and two groups can patch the same pod concurrently, the patch is an optimistic-lock merge patch (or the pod is re-read on conflict), so that a concurrent add is never undone by a concurrent remove. The early return in `addMembership` when the group's key is already present also checks `member`, so that pods labelled by an earlier controller version are backfilled on the first resync after the upgrade.

The controller remains the only writer. Whether a tenant can set the label on a pod they create is covered under Security.

### 2. The tenant baseline stops selecting members

In `packages/apps/tenant/templates/networkpolicy.yaml`, only the two allow-all CNPs change:

```yaml
# before
spec:
  endpointSelector: {}

# after (allow-internal-communication and allow-external-communication)
spec:
  endpointSelector:
    matchExpressions:
      - key: sdn.cozystack.io/member
        operator: DoesNotExist
```

Every other policy of the baseline keeps `endpointSelector: {}` or its namespace selector, so members keep:

- ingress from the API server, system namespaces, `kube-system` and parent tenants (`<tenant>-ingress`): monitoring and billing scrapes, operators, webhooks;
- egress to the tenant's namespaces and parent services (`<tenant>-egress`);
- DNS (`allow-to-dns`) and the platform services opened by the other `allow-to-*` CNPs.

Non-members are still selected by both allow-all policies and see no change. A namespace with no group has no member, so its effective policy is identical to today's.

### 3. Each managed application keeps its own flows

A member that is a replicated application must still reach its own replicas, sentinels, arbiters or control plane, and its backups must reach object storage. These flows belong to the application, not to the tenant's groups, so the application's chart renders them:

```yaml
apiVersion: cilium.io/v2
kind: CiliumNetworkPolicy
metadata:
  name: {{ .Release.Name }}-platform
spec:
  endpointSelector:
    matchLabels: &self
      apps.cozystack.io/application.group: apps.cozystack.io
      apps.cozystack.io/application.kind: Postgres
      apps.cozystack.io/application.name: {{ $appName }}   # the application name, as the lineage webhook stamps it
  ingress:
    - fromEndpoints:
        - matchLabels: *self
  egress:
    - toEntities:
        - world
```

- Ingress is opened from the application's own lineage only: its instances, sentinels, exporters, and for a Kubernetes cluster its control plane, autoscaler, CCM, CSI controller and node VMs. It does not open the application to other members of the same group.
- Egress is opened to `world` only, for backups to object storage on a public address and, for Kubernetes, for nodes pulling images. It is never `cluster` or `all`: the ingress of other tenants is open to `cluster` through their own baseline, and tenant isolation rests on egress. Egress to the tenant's own namespaces is already allowed by `<tenant>-egress`.
- For a non-member the policy is inert: the allow-all baseline already allows everything it allows.

The charts concerned are `postgres`, `mariadb`, `mongodb`, `redis`, `rabbitmq` and `kubernetes`. To keep the shape in one place, `cozy-lib` gains a helper that every application chart can include with its kind and name:

```gotemplate
{{- include "cozy-lib.network.platformPolicy" (dict "kind" "Postgres" "name" $appName "context" $) }}
```

A chart whose application needs a narrower or extra flow (a port list, a second selector) passes it to the helper instead of forking the template. Any new managed application with internal traffic includes the helper; one with no internal traffic (a VM instance) needs nothing, and was measured to restrict correctly without it.

Consequence for users: a group's egress rules have no effect on a managed application, whose egress to `world` is always open. Ingress is filtered fully. The SecurityGroup documentation says so.

### Ordering

The platform policy must exist before an application becomes a member, never after. Measured on MariaDB with its operator (below): a member without it loses replication, and the operator's automatic failover can then promote a replica that misses committed writes. Hence:

- the application charts (part 3) and the controller (part 1) ship before, or in the same release as, the tenant chart change (part 2), which is the only change that makes membership restrictive;
- consumers that attach applications on a user's behalf (APIs, dashboards) should refuse to attach a managed application whose `<release>-platform` policy is absent, which is the case for a release whose chart predates this change.

## Evidence

The prototype, on the lab described above: the two allow-all CNPs of a throw-away project namespace were changed as in part 2, the `member` label was kept by a loop in place of the patched controller, and the part 3 policy was applied by hand as the chart would render it. Probes ran from an allowed client and from a denied pod of the same namespace.

**Member without a platform policy, group ingress from the client on the service port only.** Filtering was correct for all six kinds (client allowed, other pod denied), and every replicated application broke:

| Application | Without the platform policy | With it |
|---|---|---|
| Postgres (2 instances) | 0 replicas streaming | 1 replica streaming |
| MongoDB (3 members) | cluster `state=error` | `ready` 3/3 |
| Redis (2 + 3 sentinels) | sentinels isolated, master `s_down` | 1 replica, 2 sentinels seen |
| RabbitMQ (3 nodes) | 1 node of 3 in the cluster | 3 nodes |
| Kubernetes (1 control plane, 1 worker) | autoscaler in CrashLoop, CCM restarted | node `Ready`, `logs`/`exec` through konnectivity, PVC provisioned and attached, images pulled, nested pod reaching the Internet, autoscaler stable |
| MariaDB (2 replicas, operator-managed) | replica cannot reach the primary, then failover onto the stale replica (below) | replication `Yes`/`Yes`, forced failover in 56 s with no divergence |

Recovery after adding the platform policy took about five minutes and filtering stayed correct. In the Kubernetes row the worker became a member only once `internal.cozystack.io/managed-by-cozystack` was set on its `virt-launcher` by hand, which is what cozystack/cozystack#5124 fixes.

**MariaDB, the case that sets the ordering.** With replication healthy, the application was made a member without its platform policy and a row `p1` was written. The replica could no longer reach the primary (`Connection timed out`), the MariaDB went `Ready=False` and its exporter `up=0`. About two and a half minutes later the primary's liveness probe expired, the primary was killed, and the operator failed over to the replica, which did not have `p1`. Adding the platform policy rebuilt the topology and replicated the next write, but `p1` only existed on the old primary: a committed write was lost from the live dataset. With the policy in place from the start, deleting the primary produced a new `Ready` primary in 56 s, the old one rejoined as a replica in 57 s, and the next write replicated with no divergence. (Note: an established replication connection survives a new policy, since Cilium keeps established flows; the failure appears on the next connection.)

**Platform flows.** Metrics scrapes stayed `up=1` at every phase, from the monitoring stack and from a billing scraper in system namespaces, both through `<tenant>-ingress`. DNS kept working through `allow-to-dns`. A member whose group had no egress rule lost access to the backup object store on a public address, which is why part 3 opens `world`.

**External access.** A database exposed through a Cilium load balancer with `externalTrafficPolicy: Local` was closed by its group and reopened by `fromCIDR`, with the real source address preserved up to the pod. For a Kubernetes cluster's own `LoadBalancer` Service (an infra Service without selector, created by the CCM, `externalTrafficPolicy: Cluster`), the source was preserved but the destination port seen on the node VM is the nested Service's NodePort, not its port: a rule must name the NodePort, or no port.

**Lifecycle.** A recreated pod was labelled within the second of its creation, 16 s before it was ready. Deleting a group removed its policy and labels and reopened traffic in 25-40 s. Writes took effect in 5-10 s.

## User-facing changes

- Attaching a SecurityGroup now closes the attached applications to everything their groups do not allow, except platform traffic and the application's own traffic. This is the behaviour the SecurityGroup API documents but does not deliver today.
- A managed application's egress to the outside cannot be restricted by a group.
- The SecurityGroup documentation states the limits: egress inside the tenant stays open, `toFQDNs` is not usable on kube-ovn-chained clusters, buckets are not covered, and a Kubernetes cluster's `LoadBalancer` Services are seen on their NodePort.
- No new API, field or CRD.

## Upgrade and rollback compatibility

Existing groups start restricting when the tenant chart change reaches a tenant. On clusters where tenants already created groups expecting them to be inert, this is a behaviour change for those tenants: the release notes must say so, and administrators can list existing groups (`kubectl get securitygroups -A`) before upgrading.

No migration is needed: the controller backfills `member` on its first resync, the platform policies are rendered at the next reconciliation of each application, and the tenant chart is reconciled last (Rollout).

Rollback: reverting the tenant chart makes every group inert again (open), never closed. Reverting the controller leaves stale `member` labels, which then close pods whose groups are gone; the tenant chart must therefore be reverted first, or the label stripped. Reverting an application chart while the tenant chart change is in place recreates the MariaDB failure above; the order of rollback is the reverse of the rollout.

## Security

- No new tenant-supplied input. Membership is still derived only from `spec.attachments` and lineage labels.
- `sdn.cozystack.io/member` is a closing label: setting it on a pod can only remove the baseline from that pod, never open anything. A tenant who forges it on a pod of their own closes their own pod.
- The platform policy opens ingress from the application's own lineage labels. Lineage labels can be forged by a principal who can create pods in the tenant's namespace with `internal.cozystack.io/managed-by-cozystack=true` already set, since the lineage webhook skips such pods. Such a pod becomes a peer of the application. This is the same exposure the SecurityGroup API already has for `fromApp`, it requires pod-create rights in the namespace, and it is not widened here; closing it belongs to the lineage webhook.
- Tenant isolation is unchanged: no policy added here opens egress to `cluster` or `all`, and cross-tenant attempts through groups (`toCIDR 0.0.0.0/0`, `fromCIDR 0.0.0.0/0`) remained denied on the lab, as did a node's kubelet.

## Failure and edge cases

- A new pod of an attached application, before the controller labels it → non-member, open as today; it closes when labelled (measured within a second of creation).
- Controller down → no new pod becomes a member (open), existing members stay closed by their groups; same as today's controller semantics, never wrongly closed.
- Application chart that predates part 3, attached to a group after part 2 → the application can break, and a replicated database can lose writes. Prevented by the rollout order and by consumers refusing the attachment.
- Group with no rules (cozystack/cozystack#5129) → its projected policy is invalid in Cilium 1.19, but membership works, so the member is closed to everything but platform and own-application traffic. That is the expected meaning of an empty group.
- Pod in two groups, detached from one → `member` stays; detached from the last → `member` removed in the same patch.
- Kubernetes cluster nodes before cozystack/cozystack#5124 → never members, so a group on a cluster restricts its control plane but not its nodes.
- A tenant whose own ingress controller runs in the same namespace as a member → the ingress controller's traffic to the member is intra-namespace and no longer covered by the baseline; a group must allow it. Not measured; to be covered by the e2e case.
- Traffic from another project through a public IP → seen with the source node's address, which a `fromCIDR` rule on the client's address does not match (Open questions).

## Testing

- Helm unit tests (`helm-unittest`), tenant chart: both allow-all CNPs carry the `DoesNotExist` selector; every other policy is unchanged.
- Helm unit tests, each application chart and the `cozy-lib` helper: `<release>-platform` is rendered with the application's lineage labels, ingress only from itself, egress only to `world`, and never `cluster` or `all`.
- Controller unit tests (`internal/securitygroupcontroller/reconciler_test.go`): `member` added with the first group, kept while another group remains, removed with the last; backfilled on pods that already carry a group key; no lost update when two groups patch the same pod concurrently.
- E2E (`hack/e2e-chainsaw/securitygroup/`): a new case with a member, an allowed peer and a denied peer in a tenant namespace; the denied peer is refused, the allowed peer passes on its port only, a non-member in the same namespace stays open, DNS still resolves from the member, and detaching restores access. A second case attaches a group to a two-instance Postgres and asserts that replication stays streaming.

## Rollout

1. One release ships the controller change (part 1) and the platform policy in all six application charts (part 3), with the `cozy-lib` helper. Both are inert on their own: `member` closes nothing while the baseline still selects every pod, and the platform policies only allow what the baseline already allows.
2. The next release, or the same release with the tenant chart reconciled after the applications, ships the tenant chart change (part 2). Release notes announce that SecurityGroups now restrict their members.
3. Documentation of SecurityGroups is updated with the limits listed under User-facing changes.

## Open questions

- Should Cilium's `policy-cidr-match-mode` include `nodes`? A client of another project reaching a member through a public IP is seen with its node's address (or a kube-ovn join address on the same node), so a `fromCIDR` rule on the client's own address does not match it consistently. It is a cluster-wide Cilium setting with its own consequences; this proposal does not depend on it.
- Should the platform policy's egress narrow from `world` to the configured backup endpoint when one is known? It would let group egress rules mean something for managed applications, at the cost of a per-application, per-backup-destination template.
- Should the same tenant-namespace ingress controller be part of the platform ingress for members (see Failure and edge cases), or left to groups?

## Alternatives considered

**A self rule in the tenant's group.** Adding `fromSG: [<itself>]` to the group restores replication (this is what the first lab phase used). But a group spans several applications, so it also opens every member to every other member, and it relies on every tenant knowing to write it. The application's flows belong to the application.

**A companion SecurityGroup per application, created by a consumer API.** It works with today's charts, but adds a lifecycle (create on first attach, delete on last detach, survive the application's deletion) that can drift, and a partial failure leaves a database closed and broken. The chart always renders its own flows instead.

**A Kyverno policy deriving `member` from the group labels.** A second writer of pod labels, and removing the label on detach still needs a controller. The controller that already writes membership keeps it consistent in one patch.

**A tenant-wide default-deny (upstream §8).** It gives every workload a restrictive baseline but changes every tenant at once, requires a membership admission webhook to avoid denying fresh pods, and requires carve-outs for every platform flow of every workload, not only managed applications. The targeted variant is a step towards it that changes nothing for namespaces without groups, and the platform policies it introduces are needed by both.
