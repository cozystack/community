# Managed database password rotation via a credentials-owning controller

- **Title:** `Managed database password rotation for Postgres and MariaDB via a credentials-owning controller`
- **Author(s):** `@scooby87`
- **Date:** `2026-09-07`
- **Status:** Draft

## Overview

Managed Postgres and MariaDB now generate every user password and store it only in the `<release>-credentials` Secret ([cozystack/cozystack#4078](https://github.com/cozystack/cozystack/pull/4078)). That PR also carried a `passwordRotation` counter — a tenant bumps it to regenerate every password — but review showed the counter cannot deliver its own promise while the Secret is rendered by the Helm chart: the Secret's cleartext is part of the Helm release manifest, which helm-controller retains for `MaxHistory` revisions, so a bump performed after a leak leaves the old password readable in stored history. Rotation whose purpose is to revoke, but which cannot revoke, is worse than no rotation.

This proposal moves rotation to a small controller that **owns** the credentials Secret outside the Helm render, applies the new password to the database through a short-lived Job, and reports the outcome on the app CR's status. The chart-based counter is removed from #4078; this is where it comes back, on a foundation that can actually revoke.

## Scope and related proposals

- Builds on [cozystack/cozystack#4078](https://github.com/cozystack/cozystack/pull/4078), which drops plaintext `users[].password`, always-generates passwords, and (for Postgres) already reads the password from the mounted Secret at run time instead of rendering it into the init-script SQL. That PR is the "generate + never render plaintext" half and ships independently of this proposal.
- The chart-based `passwordRotation` counter was removed from #4078 (`fix(postgres,mariadb): drop chart-based passwordRotation, defer to a controller`); this proposal is where it returns.

## Decisions

<!-- Filled in as implementation proceeds; records live under ./decisions/, numbered from 0001. -->

## Context

Today, for both engines, the chart renders `<release>-credentials` and generates each password, preserving it across reconciles with a Helm `lookup`. Passwords are applied to the database by different actors per engine: Postgres by a post-upgrade init-Job (`ALTER ROLE ... WITH PASSWORD`), MariaDB by the operator reconciling a `User` CR whose `passwordSecretKeyRef` points at the Secret. The MariaDB `root` password is applied only once, at datadir bootstrap, via `rootPasswordSecretKeyRef`. Relevant code: `packages/apps/postgres/templates/init-script.yaml`, `packages/apps/mariadb/templates/secret.yaml` and `.../user.yaml`, and the CNPG/mariadb operators.

### The problem

- A tenant rotates a password in response to a leak. The old password stays readable in up to `MaxHistory` Helm release revisions (Secrets rendered by the chart are part of the stored manifest), so the leaked credential is not retired. The tenant used to have the password in one place they could scrub; now it is in several they cannot see.
- The tenant bumps the counter and nothing observable follows — no status, no condition, no way to answer "did the new password reach the database". If the applying step fails, the Secret advertises a credential the database never received, silently.
- Rotation state is a counter compared against a value read back with `lookup`, which returns empty on any render without API access; the template cannot tell "first install" from "the read did not happen", and both regenerate and overwrite the Secret while the database keeps the old password — a silent credential split.
- Two engines, two apply mechanisms, and MariaDB `root` is excluded from rotation entirely (bootstrap-only), so the feature does not cover the most privileged account on that engine.

## Goals

- A rotation revokes: after it completes, the previous password no longer authenticates and is not retained in Helm release history.
- A rotation reports completion: the app CR carries a condition distinguishing requested / applied / failed, and the last applied counter.
- Coverage includes MariaDB `root`, or its exclusion is a deliberate, documented decision.
- Existing releases migrate without a window in which the credentials Secret is absent and without changing the live password.
- A restore into a copy converges the advertised passwords onto the recovered roles through the same completion path.

### Non-goals

- Terminating existing database sessions on rotation (a leaked password keeps working on an open connection until it closes — see Security).
- Rotating the CNPG-managed Postgres superuser (`<release>-superuser`), which is out of the chart-managed credential set.
- Removing the credential from etcd or etcd backups; the boundary is Helm's retained revisions, not all storage (see Security).

## Design

### D1 — Controller and trigger

A new `password-rotation-controller`, shipped as a `packages/system` component (engine-agnostic, must not live inside either database operator). It watches the tenant app CRs (`apps.cozystack.io/Postgres`, `MariaDB`) and reconciles their credentials Secret. The trigger is `spec.passwordRotation` (integer) on the app CR — it returns to the API, but is read by the controller, never by the chart. Discovery does not depend on the chart rendering a marker, because the controller watches the app CRs directly.

### D2 — Secret ownership and migration

The controller creates and owns `<release>-credentials`; the chart references it by name and never renders it. On the first reconcile of an existing release the controller **adopts** the current chart-rendered Secret: it reads the live Secret, strips the `meta.helm.sh/*` labels and annotations, sets its own owner, and records the adopted passwords as the baseline. Helm then no longer treats the object as part of the release, so a later chart upgrade that stops rendering it does not prune it. Ordering is guaranteed by a two-step rollout: the controller ships and adopts first; the chart change that stops rendering the Secret ships in a later release.

### D3 — Applier and completion signal

On a rotation the controller spawns a short-lived Job (reusing the existing init-Job image and pattern) that runs `ALTER ROLE`/`ALTER USER ... IDENTIFIED BY` for each managed user, reading the new password from the mounted Secret, and exits. The controller watches the Job to completion and writes the condition. This keeps database access ephemeral — a Job, as today — rather than giving the controller a standing superuser connection to every tenant database. It also makes the applier engine-uniform (SQL for both) and removes the two-writer conflict for MariaDB: the operator's `User` CR is taken out of the password loop (the Job applies passwords; the `User` CR keeps managing existence and grants).

### D4 — MariaDB root

`root` is rotated in-band by the same Job, connecting as root and running `ALTER USER 'root'@... IDENTIFIED BY ...` against the live server. `rootPasswordSecretKeyRef` stays the bootstrap-time source; the Job is what changes root afterwards. This is a deliberate decision, not an open question — without it root is uncoverable, which is the gap the chart version had.

### D5 — Restore interplay

A restore into a copy is "rotation without a bump": the recovered roles carry the source password hashes while the Secret would advertise fresh values. The controller detects a restore from the restore signal the backup driver already sets on the app (an annotation / generation, not the counter) and forces a full re-apply of every managed password through the same Job, converging the roles onto the Secret and reporting the same condition.

### D6 — RBAC

The controller needs `secrets` get/list/watch/create/update and `jobs` create in tenant namespaces. Kubernetes RBAC cannot scope `secrets` by name, so the grant is namespaced per tenant (RoleBindings managed the way other per-tenant system access is), with a dedicated ServiceAccount and an egress policy restricting the apply-Jobs to the tenant databases.

### D7 — Bootstrap ordering on a fresh install

The controller creates `<release>-credentials` on first reconcile of the app CR, before the database operator reaches datadir bootstrap. For MariaDB (root applied only once, at bootstrap) this ordering is mandatory; the controller reconciling the app on creation, and the DB operator waiting on the referenced Secret, gives the required order.

## User-facing changes

- `spec.passwordRotation: int` on the `Postgres` and `MariaDB` app CRs — the trigger. Same field name as the removed chart counter, now backed by the controller.
- `status.conditions[type=CredentialsRotation]` on the app CR, with reasons `Pending` / `Applied` / `Failed`, plus `status.observedRotation` echoing the last applied counter — the completion signal and the durable baseline in one place.
- No change to how a tenant reads a password: still `kubectl get secret <release>-credentials`.

## Upgrade and rollback compatibility

Existing clusters keep working: the controller adopts the current Secret before the chart stops rendering it, and adoption does not change the stored passwords, so the live credential is unchanged on upgrade. Migration is automatic (controller reconcile), no tenant action. Rollback: reverting the chart to a version that renders the Secret again reintroduces a Helm-owned Secret alongside the controller's — a conflict to handle in the rollout (the chart-stops-rendering release is the point of no easy return; flag it in the release notes).

## Security

New trust boundary: a central controller that can read and write the credentials Secret in every tenant namespace and create Jobs there — by construction it can reach every managed database credential, which nothing aggregates today. Mitigated by per-tenant-namespace RBAC, a dedicated ServiceAccount, and egress restriction. Rotation revokes at the database level (new password set) and overwrites the Secret; it removes the credential's retention through Helm's `MaxHistory` revisions. It does **not** remove the live Secret from etcd or etcd backups, and it does not terminate existing sessions — a leaked password keeps working on an open connection until it closes, so a full leak response also needs a session kill (`pg_terminate_backend` / `KILL`). Account coverage is per-engine: the Postgres superuser is CNPG-managed and out of scope; MariaDB `root` is in scope via D4.

## Failure and edge cases

- Apply-Job fails → condition `CredentialsRotation=Failed` with the Job's reason; `observedRotation` not advanced; retried on the next reconcile.
- Rotation requested during a restore → the restore re-apply path runs first, then the counter apply; both use the same Job and condition.
- Secret adopted twice (controller restart mid-migration) → idempotent: the strip-and-own step is a no-op once the Helm metadata is gone.
- Database unreachable at rotation time → Job blocks on readiness (as the init-Job does today), condition stays `Pending` until it succeeds or the deadline fails it.
- A leftover `spec.users[].password` in values → still ignored (the admission warning from #4078 stays); rotation operates only on the generated Secret.

## Testing

- envtest for the controller reconcile: adoption of an existing Secret, rotation on a counter bump, restore-detect force-reapply, and failure → condition.
- Unit tests for the per-engine apply-Job rendering (Postgres `ALTER ROLE`, MariaDB `ALTER USER` including root).
- e2e on a live release: bump the counter, assert the old password stops authenticating and the new one works; run a restore into a copy and assert the app-user and root logins work against the copy.

## Rollout

1. Ship `password-rotation-controller` (owns/adopts the Secret; `spec.passwordRotation` served and reconciled). Charts still reference the Secret by name.
2. In a later release, stop the charts from rendering `<release>-credentials` (the controller is now the sole owner). This is the point after which a chart-only rollback conflicts; note it in the release.

## Open questions

- **D3**: ephemeral apply-Job (proposed) vs the controller holding a direct DB connection. The Job keeps privilege ephemeral at the cost of per-rotation Job orchestration.
- **D6**: per-tenant-namespace scoping (proposed) vs a cluster-wide grant; and whether the residual "one component can reach every managed credential" trade-off is acceptable or must be scoped further.
- Returning `spec.passwordRotation` to the app API is subject to the same API-owner gate as the rest of this epic.

## Alternatives considered

- **Keep rotation in the chart (the #4078 approach).** Rejected: a chart-rendered Secret's cleartext is retained through Helm's `MaxHistory` revisions, so the bump cannot revoke; this is Helm behaviour a template cannot work around, and it is the reason for this proposal.
- **Controller holds a persistent superuser/root connection to each database.** Rejected as the default: simpler to write, but gives the controller a standing high-privilege connection to every managed database on top of the credential access; the ephemeral apply-Job (D3) keeps DB access short-lived.
- **A single apply mechanism reusing each operator's own path (Postgres init-Job, MariaDB operator User CR).** Rejected: it cannot produce a uniform completion signal, leaves MariaDB root uncovered, and for MariaDB non-root creates two writers (operator + rotation) that conflict.
