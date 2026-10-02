# Verification: what was run against the pinned versions

This file records the live checks behind the claims in [README.md](./README.md), run before any chart or API of this proposal exists. Each check used plain operator objects in a throwaway namespace on a single-instance database, or a throwaway cluster for the audit run, so the latencies are those of an idle cluster and say nothing about a loaded or highly available one. The README states each claim; this file says how it was observed.

## PostgreSQL: CNPG 1.30.0

A role was created by hand with a password and the comment `user managed by helm`, as the init-job does today. A `managed.roles` entry for it was then added to the `Cluster`, with `passwordSecret` naming an account Secret of type `kubernetes.io/basic-auth` labelled `cnpg.io/reload: "true"`.

- With the Secret missing, the role stayed `pending-reconciliation`, `status.managedRolesStatus.cannotReconcile` named the missing Secret, the old password kept working, and a requested `login: false` was not applied.
- With the Secret present and holding a SCRAM verifier, CNPG took the role over within 2 s, and the hand-set password was rejected.
- A changed verifier was applied within 2 s, and `status.managedRolesStatus.passwordStatus.<role>.resourceVersion` equalled the Secret's `resourceVersion`.
- A value that is not a canonical SCRAM verifier was hashed again as a password: only that literal string logged in, and the status said `reconciled` with no error.

## MariaDB: mariadb-operator 25.10.2

Two `User` objects pointed `passwordHashSecretKeyRef` at their own Secrets holding a `mysql_native_password` hash. One Secret carried `k8s.mariadb.com/watch`, the other did not, and both hashes were then changed.

- With the label, the new password worked within 3 s and the old one was rejected. Without it, the change was not applied within 180 s.
- With the Secret missing, a new `User` was `Ready=False` with `error reading user password hash secret`, and no SQL user was created. For an existing user the reconcile stopped the same way: the account stayed unlocked and unexpired, the old password kept working, and a changed `maxUserConnections` was not applied.

## ClickHouse: clickhouse-operator 0.25.2, ClickHouse 25.8 and 24.9

The `password_sha256_hex` of a user in a `ClickHouseInstallation` was changed to the hash of another password, on 25.8.32.4 and on 24.9.2.42, the chart's default. The new password worked after 32 s on 25.8 and after 63 s on 24.9, and the old one was rejected. The pod kept its UID and had no container restart, and the new hash was in the `chop-generated-users.xml` key of `chi-<chi>-common-usersd`. The installation still reported `InProgress` when the new password first worked. The path from an account Secret through `valuesFrom` and the chart to the installation was not run as a whole.

## Flux: helm-controller 1.5.0

A `HelmRelease` of a public chart carried `valuesFrom` entries on Secrets that did not exist yet.

- Install failed with `ValuesError` and no release, installed once the Secret appeared at the next reconcile, and refused an upgrade after the Secret was deleted again, keeping its revision and replica count. Helm stored only the value that was read.
- A `targetPath` accepts only letters, digits and `_-./\`, or `[n]`, so a user name with a comma cannot be the path: the CRD validation rejects it. The value is parsed as a Helm `--set` string, so a comma in the value fails the entry with `key "d" has no value`. A dot in the value is fine. Entries addressed by list position, `_accounts[0]`, worked, but a gap in the positions left a `null`. Entries keyed by the SHA-256 of the user name, `_accounts.u<hex>`, worked for the names `a,b` and `c.d`, which appear in neither the path nor the value, and Helm stored a map from digest to hash.

## kube-apiserver audit: v1.34.0 and v1.37.0

A stand-in aggregated API sat behind an `APIService` and answered `POST` on `credentials/mint` and `credentials/revoke` with a password-like marker in the response, and a list with another. The policy was `RequestResponse` for one group and `Request` for another, and a ConfigMap at `RequestResponse` served as the control.

- Every request body reached the stand-in.
- The audit file holds each call with its level and `objectRef.subresource`, but no `requestObject` and no `responseObject`, and none of the marker strings sent in the requests or returned in the responses appears anywhere in it.
- The ConfigMap event carried both objects, so the policy was live and the check could have found a body.

## Still to run

- The same checks through the converted charts and the Cozystack API.
- ClickHouse's whole path from an account Secret through `valuesFrom` and the chart to the engine.
- The audit run with a `RequestResponse` rule on `core.cozystack.io` on the pinned stand. The stand-in matches the real API only as far as kube-apiserver treats every proxied request alike.
