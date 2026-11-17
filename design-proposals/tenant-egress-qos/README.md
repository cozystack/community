# Per-tenant egress addresses and aggregate bandwidth

- **Title:** Per-tenant egress addresses and aggregate bandwidth
- **Author(s):** @yankawai

## Overview

Give an opted-in tenant one egress path with its own address and a bandwidth ceiling shared by its workloads. Start with Kube-OVN VPC NAT Gateway and QoSPolicy. Managed Kubernetes workers must use the tenant VPC for this to cover their guest pods. Existing tenants keep their current network until explicitly migrated.

This addresses [cozystack/cozystack#3755](https://github.com/cozystack/cozystack/issues/3755). The decision requested is whether a single active NAT gateway with connection interruption during recovery is an acceptable first delivery, or whether stateful failover must precede it.

## Scope and related proposals

The [IP address claims proposal](../ip-address-claims/README.md) owns address allocation and retention. This proposal needs a NAT consumer/provisioner integration with that model; a MetalLB Service reservation does not by itself attach an address to Kube-OVN SNAT. Tenant-facing arbitrary external IP strings must not become an alternative allocator.

## Decisions

## Context

The managed worker network described in #3755 uses the shared pod network. Adding a NAT gateway to a separate VPC does not redirect those workers. Pod bandwidth annotations also do not impose one ceiling across a tenant's replicas and node groups.

An isolated test on Kube-OVN v1.15.10 used a private VPC, one NAT gateway, one EIP, one SNAT rule and a NATGW QoSPolicy with ingress and egress set to 10 Mbit/s on its external interface. TCP transfers ran for 15 seconds, with each client offering at most 15 Mbit/s. Rates below were measured at the receiver on an isolated external subnet, not over the public internet.

| Direction | One client | Two clients, aggregate |
| --- | --- | --- |
| Outbound | 9.95 Mbit/s | 10.04 Mbit/s |
| Reply traffic | 10.35 Mbit/s | 10.23 Mbit/s |

The observed source was the assigned EIP. Recreating the gateway pod restored new connections with that EIP. This did not test node loss, retained connections or public-uplink capacity. The short measurements include burst allowance and do not establish a strict instantaneous cap.

The same experiment with VpcEgressGateway and pod QoS limited outgoing traffic through one replica, but reply traffic exceeded the cap, and two active replicas allowed approximately twice the configured rate. `QoSPolicy.shared` allows policy reuse; it does not create a distributed bandwidth bucket.

## Goals

- A tenant's selected workloads share one address and one ceiling in each direction, independent of replica count.
- Another tenant cannot consume that address, bypass the ceiling or alter the gateway policy.
- Platform image pulls, backups and control-plane traffic retain a separate path and capacity budget.

## Design

The platform owns the gateway, external attachment, address binding, SNAT rule, subnet routes and QoSPolicy. Tenant inputs reference their own VPC and address claim and request ingress/egress limits within an operator-controlled maximum. The controller rejects cross-tenant references and unsupported address backends before changing routes.

All subnets covered by one tenant ceiling route through one active gateway and one aggregate policy. Additional VPCs must either join that enforcement point or be reported as outside the tenant-wide ceiling; creating another gateway must not silently grant another copy of the limit. Internal tenant traffic is excluded. Reply traffic for outbound connections is covered; unsolicited public ingress through LoadBalancer Services is a separate path and is not covered by SNAT shaping.

Managed Kubernetes requires an explicit VPC attachment for worker VM interfaces and guest routing. That change must preserve worker-to-control-plane connectivity, DNS, MTU, image downloads, autoscaling and supported live migration. Admission must prevent a tenant from restoring an unrestricted default route through an additional shared-network interface. Merely adding a second NIC is insufficient.

The initial gateway is one active pod. Its replacement may restore the address and rules but drops existing connection state. Two active pods with separate rate limits are not an HA implementation of the same aggregate ceiling. Stateful HA needs a separate ownership/fencing and conntrack design before it can be advertised.

The physical uplink still needs an operator-owned aggregate cap and protected capacity for platform traffic. Tenant ceilings alone cannot protect an uplink when many tenants transmit together. Classification must come from trusted addresses or interfaces, not tenant-supplied priority markings. The network provider's mitigation profile must accept legitimate aggregate traffic independently of this feature.

## User-facing changes

An opted-in tenant sees its assigned egress address, effective limits and gateway readiness. Address bound, policy applied and traffic ready are distinct conditions. Gateway failure must not silently fall back to the shared egress address. The exact field placement on VPC, tenant and Kubernetes node-group APIs remains a review decision.

## Upgrade and rollback compatibility

The default is unchanged. Start with a new test VPC and new worker group; do not rewrite existing VM networks in place. Migrate workloads only after the new path passes acceptance. Keep the old worker group until rollback has been tested, and detach the address only after traffic has left the gateway. Rollback interrupts connections and may change the observed source address.

## Security

Cluster-scoped Kube-OVN resources and external NADs remain platform-owned. Tenants receive no write access to gateway workloads, QoSPolicy or provider subnets. Address entitlement is checked by tenant namespace, including requests made by a privileged controller on its behalf. A missing gateway fails closed; route and policy reconciliation must not grant unrestricted fallback.

## Failure and testing

Acceptance must cover one and several workers, several node groups and subnets, simultaneous bidirectional traffic, a second tenant, reconnects after pod and node failure, stale gateway ownership and rollback. Measure receiver throughput, packet loss, gateway resources, connection interruption and source IP. Check sustained traffic separately from configured bursts, and test the intended production rate before publishing a capacity claim.

Also verify that worker growth cannot bypass the ceiling, a tenant cannot attach another tenant's claim, platform downloads and backups progress under tenant saturation, and configuration errors surface in status without changing a working route.

## Rollout

After design acceptance, implement address binding and gateway ownership first, then managed-worker attachment and reconciliation. Deliver as opt-in only after the failure and rollback tests pass. HA remains unavailable unless its ownership and connection-state contract is implemented and tested.

## Open questions

1. Is connection-breaking recovery acceptable initially, or is stateful HA required before release?
2. Which existing controller owns the NAT consumer and enforces one ceiling across a tenant's VPCs?
3. Should the first API cover only gateway traffic, or must public LoadBalancer ingress share the same ceiling?

## Alternatives considered

Per-pod limits multiply with replicas. Several active VpcEgressGateway replicas with independent limits have the same problem. Cilium egress gateway would change the existing CNI/masquerading contract and does not itself supply this tenant aggregate. Border shaping by a dedicated tenant address is viable where the network operator supports it, but still needs tenant address ownership and correct routing from managed workers. A single cluster-wide limit protects an uplink but cannot provide tenant isolation.
