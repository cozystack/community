# OpenBAO self-initialization

- **Title:** `Self-initialization of the managed OpenBAO app`
- **Author(s):** `@yankawai`

## Overview

A managed OpenBAO instance starts uninitialized and sealed and stays NotReady until someone runs `bao operator init` against its API. This proposes an opt-in `bootstrap` for `packages/apps/openbao` that uses the `initialize` stanza of OpenBAO 2.5.1, the version the app runs, on top of the static auto-unseal from cozystack/cozystack#4766. The instance initializes itself on first start, the root token never leaves the server, and the tenant gets an admin login in a Secret, the way other apps hand out credentials.

## Scope and related proposals

This covers the init part of cozystack/cozystack#2787 for the tenant app. TLS for the listener and Raft is the other half of that issue and stays a separate change, see [unified-tls-pki](../unified-tls-pki/README.md). The central transit instance in cozystack/cozystack#4177 is neither required nor blocked by this.

## Context

`packages/apps/openbao/templates/openbao.yaml` renders the upstream chart. The seal is Shamir by default. `seal.type: static` mounts a key from `seal.secretName` with `seal.keyId` and unseals on every restart, but a platform admin has to create that Secret, and init is still manual. The chart already refuses a silent seal change by reading the seal from the previous `<release>-config` ConfigMap. The listener runs with `tlsDisable: true`.

The readiness probe is `bao status`, so an uninitialized pod is NotReady forever. Tenant roles have no `exec` or `port-forward`, so a tenant can only init through the API, from a VM in the same tenant or through `external: true`, which is plain HTTP today.

### The problem

A tenant orders OpenBAO from the dashboard and gets an app that never becomes Ready, with nothing in the dashboard to finish it. Whoever reaches port 8200 first can init it and keeps the root token. With `external: true` that token goes over plain HTTP.

A post-install Job that calls `sys/init` and stores the answer in a Secret looks like the obvious fix, but the two calls are not atomic. If the Job dies between them, the instance is initialized and the root token is lost.

## Goals

- With `bootstrap.enabled: true` the instance becomes Ready and unsealed with no human step.
- The tenant gets a working admin login in a Secret in the app namespace.
- No root token is returned over the API, written to a Secret or printed to logs.
- Instances without `bootstrap` render exactly as today.

### Non-goals

- TLS, see Scope.
- Changing the seal of an existing instance.
- A shared KMS or transit service for all tenants.

## Design

### Values

```yaml
bootstrap:
  enabled: false
```

`bootstrap.enabled` requires `seal.type: static`, the chart fails the render otherwise, because self-initialization needs auto-unseal. For the first version it also requires `replicas: 1`, see Open questions.

### Seal key

With `bootstrap.enabled` and no `seal.secretName`, the chart creates `<release>-seal` with a random 32-byte key and `keyId: bootstrap`. The key is generated once and kept on every later render through `lookup`. If `<release>-config` exists from an earlier release and the key Secret does not, the render fails instead of generating a new key, because a new key cannot unseal existing data. Both Secrets of this proposal carry `helm.sh/resource-policy: keep`.

### Admin login

The chart creates `<release>-admin` with a random password, generated once in the same way, and mounts it into the server pod. The `initialize` stanza enables userpass, writes an admin policy and creates the user, reading the password from the mounted file:

```hcl
initialize "admin" {
  request "enable-userpass" {
    operation = "update"
    path      = "sys/auth/userpass"
    data      = { type = "userpass" }
  }
  request "admin-policy" {
    operation = "update"
    path      = "sys/policies/acl/admin"
    data      = { policy = "path \"*\" { capabilities = [\"create\", \"read\", \"update\", \"delete\", \"list\", \"sudo\"] }" }
  }
  request "admin-user" {
    operation = "update"
    path      = "auth/userpass/users/admin"
    data = {
      password = {
        eval_type   = "string"
        eval_source = "file"
        path        = "/openbao/userconfig/<release>-admin/password"
      }
      token_policies = ["admin"]
    }
  }
}
```

OpenBAO runs this once, on the first start, with a temporary root token that it revokes afterwards. The `<release>-admin` Secret is shown in the app's secrets tab, like the credentials of other apps. If it is missing from an existing bootstrap release, rendering fails instead of generating another password: `initialize` does not update credentials in initialized storage.

## User-facing changes

A `bootstrap.enabled` switch in the app form. After the app is Ready, the secrets tab has the admin login. Self-initialization generates no recovery keys. The app docs explain how the admin gets them through the authenticated [recovery key rotation API](https://openbao.org/api-docs/system/rotate/).

## Upgrade and rollback compatibility

The default is off, existing releases render the same. OpenBAO runs `initialize` only on the first start, so turning `bootstrap` on for an instance that is already initialized would hand out a login that does not exist. The chart refuses that when the `<release>-config` of the earlier release has no `initialize` stanza. Turning it off later keeps the instance and both Secrets, the release then has to name the key explicitly with `seal.secretName: <release>-seal` and `seal.keyId: bootstrap`.

## Security

- The seal key and the admin password live in Secrets of the tenant namespace. Whoever can read them can unseal the data and log in, including cluster admins. This is the trust model of the static seal today, now explicit.
- The root token exists only inside the server during initialization.
- The chart enables no audit device with `log_raw` and does not set `TRACE`, at `TRACE` OpenBAO logs request bodies, the password included.
- Until TLS lands, bootstrap rejects `external: true`; a warning does not protect credentials sent over HTTP. Internal HTTP access still requires a trusted tenant network.

## Failure and edge cases

- Initialization stops halfway. Preserve the PVC and both Secrets and report the failed step; do not automatically delete storage or repeat init. Recovery from failure before the admin user exists is an implementation gate, because there may be no usable login or recovery keys.
- The key Secret is deleted. The instance stays sealed after the next restart and the chart refuses to make a new key.
- A backup is restored into a new instance. Before starting it, restore the original seal Secret and the admin Secret matching the credentials in that backup. Initialized storage keeps the source password hash and skips `initialize`; a newly generated admin password cannot log in. If the password was rotated after the backup, use the matching saved credential or a separately verified authentication-recovery path. Preserve both Secrets with the backup recovery material under restricted access.

## Testing

`hack/e2e-apps/openbao.bats` gets a bootstrap case. The app becomes Ready without a manual init, the password from `<release>-admin` logs in, a KV secret is written and read, the pod is restarted, it unseals by itself and the login still works. Negative cases cover bootstrap without a static seal, bootstrap with `replicas` above 1, external access without TLS, a missing admin Secret on an existing release, and an existing Shamir instance rendering unchanged. A restore test writes a KV secret, backs up the data, restores it with the matching seal and admin Secrets into a fresh instance, then verifies login and the original value. A mismatched admin password must fail authentication; it must not trigger reinitialization or storage deletion.

## Open questions

- HA. When `replicas` is above 1, does a Raft follower skip `initialize` when it joins with `retry_join`, or can two pods initialize two clusters? Until this is verified on a real cluster, bootstrap is limited to `replicas: 1`.
- Login method. Userpass is the simplest. Kubernetes auth bound to a tenant ServiceAccount avoids a password but needs the tenant to have a workload first.
- Recovery keys. Rotate them right after bootstrap and put them in a second Secret, or leave that to the tenant.
