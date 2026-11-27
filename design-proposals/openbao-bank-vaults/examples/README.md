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
   additionally enforce `existingClaimRef` at Pod admission and protect the approved
   PVC from deletion while active; see the proposed controls and tests in the design.
   The external records are
   `team-a/secret/data/unseal/main/vault-unseal-0` through `vault-unseal-4`.
   Each record has a same-named field containing standard base64 of the original share
   text. For example, synthetic text `test-share` in record `vault-unseal-0` is written as
   `{"data":{"vault-unseal-0":"dGVzdC1zaGFyZQ=="}}`. This is not a real unseal key.
   Verify `base64.b64decode("dGVzdC1zaGFyZQ==").decode() == "test-share"` in Python;
   do not paste raw share text into that field or reuse shares with unrelated data.
7. Provide primary ConfigMap `external-openbao-ca` with `ca.crt`, and Secret
   `openbao-main-tls` with `tls.crt`, `tls.key`, `ca.crt`. The operator maps the server
   files to `server.crt` / `server.key`. The primary certificate needs IP SAN `127.0.0.1`
   for the unseal sidecar plus the DNS SANs used by clients and peers. The external
   certificate must cover its configured DNS name.

The reference inherits `preFlightChecks: false` from the tested configuration.
This is not a proposed production default; the integration must review it and retain
independent permission, connectivity and recovery tests.
The external server's own key protection must not depend on the primary server.
The init-container CA bundle is a snapshot. Stage old and new CAs and replace the Pod;
switch the external server to its new certificate and verify the served chain and JWT
login. Then remove the old CA, replace the Pod again and verify unseal with the final
bundle. Restarting only the sidecar does not rebuild it. Plan both restarts for the
single-replica instance. Before using restored data with the declared audit device,
follow the design's staged restore procedure and reserve an unused audit path.

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
- A platform identity authorized for the specific mount, policy and role operations
  below. Application identities must not receive these administrative permissions.
  The fragment contains configuration data only; it supplies no credentials.
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

Provisioning sequence (separate from unseal):

1. Platform operators provision the external key-store namespace, mounts, JWT auth,
   policy and role using the external-unseal steps above. The reference CR has no
   `externalConfig`, so it creates no configurer and provisions no application roles.
2. On the primary server, authenticate using an approved administrative workflow;
   acquire any root credential in root before targeting `team-a`. Use namespace-aware
   API calls with `X-Vault-Namespace: team-a` for the following operations.
3. Read `sys/mounts` and require `secret/` to be unused or already KV v2. Create it via
   `POST sys/mounts/secret` with `{"type":"kv","options":{"version":"2"}}` only if absent.
   Reject a different type or KV v1; use a new mount or an explicit migration instead.
4. Write the two named policies to `PUT sys/policies/acl/<name>` with `{"policy":"..."}`.
   Write each role's fields to `POST auth/kubernetes-workloads/role/<name>`. Do not write
   `auth/kubernetes-workloads/config`; read it before/after and verify it is unchanged.
   The approved workflow needs read on mount/auth inspection and write on these exact
   administrative paths, including sudo where the endpoint requires it. No wildcard
   application-data or key-store access is needed. A packaged applier is a future gate.

Stock `bank-vaults configure` is a separate privileged alternative, not a command for
this incomplete fragment. Complete its intended Kubernetes host, CA and TokenReview
settings explicitly: omitting `auth.config` rewrites the connection using local cluster
settings. With `VAULT_NAMESPACE=team-a`, the shares-only branch calls a root-only
`generate-root` endpoint and fails. A working namespaced invocation needs a separately
acquired root token available through its configured key store (`storeRootToken: true`),
plus dedicated key-store authentication, CA trust and target namespace configuration.
Do not add this fragment to the CR and assume the separate configurer inherits the
unsealer's projected JWT or assembled CA bundle; it does not.

Reloader opt-in belongs in the workload's Pod-template annotations. It reads KV data
responses and updates the template; it does not rotate credentials or update live env.
Its upstream README still labels it early alpha. Manual rollout remains an alternative.
Use one controller and one OpenBao server/namespace for all opted-in workloads in this
Kubernetes cluster. References are grouped by path only; mixed servers/namespaces are
outside this profile. Adding controllers does not give per-instance workload selection.
On first startup or after downtime, the first poll establishes a baseline without rolling
existing Pods. If a secret changed before that poll, establish the baseline, manually
roll affected consumers and verify the intended version. Coordinate concurrent updates.
To opt out or pin/remove the last unversioned reference on an already tracked workload,
stop Reloader, apply the change, then restart with an empty cache. Annotation/reference
changes alone can leave stale associations. Recover missed changes as above; include
both initial-startup and cache-reset transitions in acceptance tests.
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
checked with its pinned mergo 0.3.16. A separate OpenBao 2.7.2 test verified role-only
API writes preserve auth connection settings, namespaced generate-root is rejected,
and a snapshot restores into an independent Raft volume with the original keys/data.
The restored API audit file/ remained enabled alongside the new declared stdout sink;
health and audit output were checked. PEM assembly was tested with and without a
trailing newline in the first input. These synthetic HTTP/admin-token tests do not
validate Kubernetes rendering/admission, TLS/JWT integration or production recovery.

Runtime acceptance must cover bootstrap, scoped auth, injection, secret-change rollout,
TLS renewal, failure handling and isolated restore. The [proposal](../README.md) lists
the gates before presenting this as a supported Cozystack package.
