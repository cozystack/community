# Reference configurations

These examples use fictional identities and endpoints. They are review material,
not a standalone installation package. Do not apply this directory recursively.
See [the proposal](../README.md) for ownership, security and acceptance criteria.

## Existing and proposed APIs

| File | Purpose |
| --- | --- |
| [current-app.yaml](./current-app.yaml) | Existing Cozystack OpenBAO application schema. |
| [proposed-app.yaml.example](./proposed-app.yaml.example) | New API sketch, unsupported today; do not apply. |
| [vault-cr-example.yaml](./vault-cr-example.yaml) | Actual Vault Operator API with Raft, TLS, audit and external unseal. |

The Vault CR is an independent reference, not generated output from the proposed API.
It requires Vault Operator 1.24.1 and namespace `tenant-openbao`, plus the dependencies
below. Its read-only unseal identity cannot initialize an empty server and persist keys.
For the existing Cozystack example, the namespace must already be provisioned.
The current single-replica app uses file storage; it is not the same storage profile
as the Raft-based operator reference.

## External unseal

1. Provision an independently recoverable external OpenBao at
   `https://unseal.example.org:8200`, with trusted TLS and working DNS/network access.
2. Create OpenBao namespace `team-a`, KV v2 mount `secret/`, and root JWT mount
   `main-kubernetes/` on that external server.
3. Configure the actual primary Kubernetes issuer/signing keys using
   [external-auth-config.json.example](./external-auth-config.json.example).
   The placeholder public key must be replaced; do not submit it unchanged.
4. Install root policy `main-unseal-reader` from
   [external-reader-policy.hcl](./external-reader-policy.hcl), and root JWT role
   `main-unsealer` from [external-jwt-role.json](./external-jwt-role.json).
5. Create [the unsealer ServiceAccount](./service-account.yaml) in the primary cluster.
   The Vault CR projects a short-lived JWT with audience `external-openbao-unseal`.
6. Provision matching primary Raft data and external shares through a separately
   controlled bootstrap/restore procedure. For this standalone CR, pre-provision
   PVC `raft-openbao-main-0` in `tenant-openbao`; record and verify its UID, Bound
   state and restore provenance before creating the CR. The stock operator does not
   validate restore identity or block dynamic provisioning if that PVC disappears.
   The example appends `--init=false` to prevent initialization of empty storage;
   verify the rendered sidecar args after upgrades. A Cozystack integration must
   additionally enforce the proposed `existingClaimRef` guard before CR creation.
   The external records are
   `team-a/secret/data/unseal/main/vault-unseal-0` through `vault-unseal-4`.
   Values must use the encoding expected by Bank-Vaults 1.33.2; validate using synthetic
   data before provisioning real keys. Never reuse shares with unrelated initialized data.
7. Provide primary ConfigMap `external-openbao-ca` with `ca.crt`, and Secret
   `openbao-main-tls` with `tls.crt`, `tls.key`, `ca.crt`. The operator maps the server
   files to `server.crt` / `server.key`. Certificate SANs must cover the actual server
   endpoints; the external certificate must cover its configured DNS name.

The reference inherits `preFlightChecks: false` from the tested configuration.
This is not a proposed production default; the integration must review it and retain
independent permission, connectivity and recovery tests.
The external server's own key protection must not depend on the primary server.
The init-container CA bundle is a snapshot: stage old and new CAs together, replace
the Pod, verify external JWT login/unseal, then retire old trust. Restarting only the
unsealer sidecar does not rerun the init container or refresh this bundle.

## Workload injection and reload

| File | Purpose |
| --- | --- |
| [application-config-fragment.yaml](./application-config-fragment.yaml) | KV engine, application and Reloader policies/auth roles. |
| [webhook-values.yaml](./webhook-values.yaml) | Scoped Pod mutation, webhook chart 1.23.1 and vault-env 1.23.0. |
| [workload.yaml](./workload.yaml) | Application ServiceAccount and Deployment with secret references. |
| [reloader-values.yaml](./reloader-values.yaml) | Optional experimental Reloader 0.5.1. |

Prerequisites:

- Workload Kubernetes namespaces `demo-app` and `bank-vaults`, cert-manager, and network
  access to the primary OpenBao at `https://secrets.example.org:8200`.
- OpenBao namespace `team-a`, with a configured `kubernetes-workloads` auth connection
  to the actual workload API/CA and a reviewed TokenReview identity/audience.
- Privileged platform configurer credentials: stock Bank-Vaults 1.33.2 configure
  retrieves or generates a root token. Namespace-scoped credentials alone do not work.
  The fragment contains no connection setup, root tokens or application secret values.
  A future scoped API applier would target `sys/mounts/secret`, the two named
  `sys/policies/acl/*` policies and `auth/kubernetes-workloads/role/*` roles in `team-a`,
  with only the required administrative read/write permissions. Auth connection setup
  remains a separate platform operation. This applier is not implemented by this PR.
- Secret `openbao-client-ca`, key `ca.crt`, in both workload namespaces.
- Install the webhook in `bank-vaults`, outside selected namespace `demo-app`; its
  default `ignoreReleaseNamespace: true` otherwise skips the application Pods.
- Reloader 0.5.1 needs cluster-wide get/list/watch on Deployments, StatefulSets and
  DaemonSets for discovery, plus namespace-scoped get/update for workload changes and
  a separate CA Secret get grant. Keep it disabled if cluster-wide reads are unacceptable.
  The example values do not constrain default chart ClusterRoleBindings; the integration
  must implement and verify these boundaries.
- Match each auth role audience to `aud` in its client's actual JWT, also accepted by
  TokenReview; do not substitute an arbitrary API-accepted audience.
- Securely provisioned `API_KEY` at `secret/data/demo/api` within the primary `team-a`.
- A real application image/entrypoint and `/ready` endpoint replacing the placeholders.

The two servers' `team-a` namespaces are independent. An OpenBao namespace, a Kubernetes
namespace and a KV path are different objects.

Reloader opt-in belongs in the workload's Pod-template annotations. It reads KV data
responses and updates the template; it does not rotate credentials or update live env.
Its upstream README still labels it early alpha. Manual rollout remains an alternative.
If a secret changes during Reloader downtime, the new version becomes its baseline
when it returns; existing Pods may retain the old value. Manually roll affected consumers.
To disable already tracked workloads, stop all relevant Reloader instances, remove opt-in,
then restart Reloader with empty caches. Removing the annotation alone is insufficient
in v0.5.1. Include both sequences in acceptance tests.
The workload must tolerate overlap during RollingUpdate; two replicas do not by
themselves guarantee availability or safe credential rotation.

## Validation boundaries

The Vault CR was checked against the official operator 1.24.1 CRD; the current-app
example against the reviewed Cozystack application schema. YAML/JSON parsing,
annotation names, role/ServiceAccount/audience/path agreement and Helm value names
were checked. These checks do not constitute deployment of the sanitized examples.
A local synthetic Docker test with OpenBao 2.7.2 / Bank-Vaults 1.33.2 verified that
`--init=false` leaves empty Raft storage uninitialized and unseals retained data after
restart through a read-only external KV identity. The operator's merge options were
checked with its pinned mergo 0.3.16. This test used HTTP/static tokens; it does not
validate Kubernetes rendering, TLS/JWT, restore identity or full snapshot restore.

Runtime acceptance must cover bootstrap, scoped auth, injection, secret-change rollout,
TLS renewal, failure handling and isolated restore. The [proposal](../README.md) lists
the gates before presenting this as a supported Cozystack package.
