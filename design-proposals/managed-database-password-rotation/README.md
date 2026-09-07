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

The controller ends up the sole owner of `<release>-credentials`; the chart references it by name and, after migration, never renders it. Adoption cannot be done by stripping `meta.helm.sh/*` from a Secret the chart still renders: release membership lives in the Helm release storage (`sh.helm.release.v1.*`), not in the object's annotations, and under helm-controller's server-side apply (`serverSideApply=true` since v1.5.0) Helm owns the `data` field in `managedFields` — so while the chart still renders the object, every reconcile re-stamps the annotations and overwrites `data` with the chart-generated password, and the controller cannot durably take the field. Ownership only frees up when Helm itself relinquishes it, which happens when the chart stops rendering the object. Migration therefore runs in this order, and the ordering is what the two-step rollout enforces:

1. **Release A — pin, don't fight.** The chart stamps `helm.sh/resource-policy: keep` on the still-rendered `<release>-credentials` (the cozystack-standard orphan mechanism) and continues to render it. The controller ships and begins reconciling the app CRs, but it does **not** write `data` on a Secret Helm still owns — it only observes and records the current passwords as its baseline. There is no field tug-of-war, because only Helm writes `data` in this release.
2. **Release B — hand off.** The chart stops rendering `<release>-credentials`. Because of `resource-policy: keep`, Helm orphans the object instead of pruning it (so there is no window where the Secret is absent), and Helm releases its `managedFields` ownership of `data`. The controller now adopts the orphan: it sets its own owner reference and labels, takes over `data` under its own field manager, and from here is the sole writer.

On a fresh install there is nothing to adopt; see D7 for the create path. The controller branches on whether a Helm-owned `<release>-credentials` already exists: present → adopt (existing release), absent → create (fresh install), so D2 and D7 never both claim the ordering for the same object.

### D3 — Applier and completion signal

On a rotation the controller spawns a short-lived Job (reusing the existing init-Job image and pattern) that runs `ALTER ROLE`/`ALTER USER ... IDENTIFIED BY` for each managed user and exits. The controller watches the Job to completion and writes the condition. This keeps database access ephemeral — a Job, as today — rather than giving the controller a standing superuser connection to every tenant database. It also makes the applier engine-uniform (SQL for both).

**Apply before advertise.** Neither engine supports two live passwords for one role, so the new password must reach the database before it becomes the value a tenant reads. The controller does not overwrite the live `<release>-credentials` key first. It generates the new password into a controller-held **staging** Secret (`<release>-credentials-pending`, not tenant-facing), mounts *that* into the apply-Job, and only after the `ALTER` for a given user succeeds does it copy that user's new value into the live `<release>-credentials`. So the live Secret never advertises a password the database has not already accepted — closing the happy-path window and the unbounded window when the database is unreachable (the Job stays `Pending`, the live Secret keeps the old, still-valid password).

**Per-user commit, not an all-or-nothing scalar.** The Job applies users in sequence and the controller commits each to the live Secret as its `ALTER` succeeds. `status.observedRotation` advances to the requested counter only once **every** managed user has been applied; until then the condition is `Pending` and carries the set of users still outstanding. A partial failure (user A applied, user B fails) therefore leaves both the applied and the not-yet-applied users in a consistent Secret-matches-database state — A on the new password in both places, B on the old password in both places — rather than a Secret that advertises a credential the database never received. The staging Secret is cleared once `observedRotation` reaches the counter.

**Two writers removed for MariaDB.** The operator's `User` CR is taken out of the password loop (the Job applies passwords; the `User` CR keeps managing existence and grants). Concretely this is a chart change, shipped with Release B (D2): the chart renders the `User` CR **without** `spec.passwordSecretKeyRef`, so mariadb-operator no longer reconciles the password from the Secret and the Job is the sole password writer. See D8 for the ordering constraint this creates (a user must exist before the Job can `ALTER` it, and must never be left with an operator-defaulted empty password).

### D4 — Applier authentication and MariaDB root

The apply-Job must itself authenticate to the live database with a credential valid *at apply time*, and this is separate from the managed passwords it is rotating. The source is per engine:

- **Postgres.** The Job authenticates as the CNPG-managed superuser, read from `<release>-superuser` (the Secret CNPG owns, outside the chart-managed credential set), and runs `ALTER ROLE` for each managed user. The superuser is out of scope for rotation (a Non-goal), but it is the Job's login. This adds one narrow RBAC grant: the Job's ServiceAccount reads `<release>-superuser` in the tenant namespace.
- **MariaDB.** The Job authenticates as `root`. `root` is rotated in-band by the same Job (`ALTER USER 'root'@... IDENTIFIED BY ...`), so the Job connects with the *current* root password, rotates every managed user, and rotates root last. `rootPasswordSecretKeyRef` stays the bootstrap-time source; the Job is what changes root afterwards. Covering root is deliberate — without it the most privileged account on the engine is uncoverable, the gap the chart version had.

Because the Job logs in with a privileged credential, its `activeDeadlineSeconds`, `backoffLimit`, and `ttlSecondsAfterFinished` are bounded (D3/edge cases) so a failed or blocked Job with that credential mounted does not linger.

### D5 — Restore interplay

A restore into a copy is "rotation without a bump": the recovered roles carry the source password hashes while the target's freshly generated Secret advertises different values. The controller detects a restore from the signal the backup driver already sets on the app (an annotation / generation, not the counter) and forces a full re-apply of every managed password through the same Job, converging the roles onto the target Secret and reporting the same condition.

The subtlety the applier login (D4) creates: right after a restore, the live privileged credential is the **source's**, not the target's. The Job must log in with the value the recovered datadir actually carries, then rotate forward.

- **Postgres.** CNPG re-establishes `<release>-superuser` for the copy as part of standing the target up, so the superuser the Job reads is valid against the restored instance — the app-role re-apply works without a source-credential handoff.
- **MariaDB.** `root` in the recovered datadir is the *source's* root password (bootstrap-only; the target's fresh `rootPasswordSecretKeyRef` was never applied to a datadir that already exists). The controller therefore obtains the source root password from the source credentials the restore already carries (the backup driver provisions the copy from the source, so the value is available on the restore path), logs in with it, then `ALTER`s root to the target's fresh value and re-applies the managed users. If a given restore path does not expose the source privileged credential to the controller, MariaDB root re-apply after a restore-into-copy is out of scope for that path and the condition reports it rather than silently diverging — see Open questions.

### D6 — RBAC

The controller needs `secrets` get/list/watch/create/update and `jobs` create in tenant namespaces. Kubernetes RBAC cannot scope `secrets` by name, so the grant is namespaced per tenant (RoleBindings managed the way other per-tenant system access is), with a dedicated ServiceAccount and an egress policy restricting the apply-Jobs to the tenant databases.

### D7 — Bootstrap ordering on a fresh install

On a fresh install the controller creates `<release>-credentials` (D2's create branch) before the database operator reaches datadir bootstrap. For MariaDB, where `root` is applied only once at bootstrap, this order is mandatory: bootstrap must read the controller-written root value, not race it. The order is not left to reconcile timing; it is serialized by a hard dependency in the render graph.

The DB workload is rendered so that its bootstrap **blocks on the Secret existing**, and the Secret is created by the controller, not the chart. Both operators already gate bootstrap on their credential Secret reference (MariaDB `rootPasswordSecretKeyRef`, CNPG the bootstrap secret) — a referenced-but-absent Secret holds the operator before it initialises the datadir rather than letting it proceed with a default. The chart references `<release>-credentials` by name (never renders it), so on a fresh install the object does not exist until the controller creates it, and the operator waits. This must be verified per operator during implementation (that a missing referenced Secret blocks, and does not error the resource into a terminal state) — see Testing; if an operator does not block, an explicit init-gate (an ownerRef barrier or a `dependsOn` on the controller-owned Secret) is added for that engine.

### D8 — MariaDB `User` CR and the empty-password hazard

Taking the operator out of the password loop (D3) means the chart renders the `User` CR without `spec.passwordSecretKeyRef`. mariadb-operator then manages the user's existence and grants but never sets or resets its password, and the apply-Job is the sole writer. This creates one ordering constraint the controller must honour: a user must exist before the Job can `ALTER` it, and a user must never be observable with an operator-defaulted or empty password in the gap between "operator created the user" and "Job applied the password". The controller sequences a create as create-user-then-apply within the same reconcile, applying the password before the user is announced as ready; a `User` recreated by the operator (drift, restore) is re-detected and re-applied through the same restore/force path (D5) rather than left on whatever default the operator produced.

### D9 — Concurrency and idempotent generation

The controller runs **one** apply-Job per app CR at a time (single-flight), guarded by leader election so multiple controller replicas do not each spawn a Job. Concurrent or rapid bumps (`1 → 2 → 3` while a Job for `2` is in flight) do not stack Jobs: the running Job is watched to completion or superseded, and the controller then reconciles to the latest observed counter. Generation is made idempotent by the staging Secret (D3): the new passwords for a given counter are generated **once**, into `<release>-credentials-pending` keyed by the counter value, and re-reconciling the same counter reuses the staged values rather than regenerating — so a mid-flight re-reconcile applies the same passwords it started with, and `observedRotation` moving to the counter is the single fact that ends the cycle. A newer counter regenerates the staging set and restarts the single Job.

## User-facing changes

- `spec.passwordRotation: int` on the `Postgres` and `MariaDB` app CRs — the trigger. Same field name as the removed chart counter, now backed by the controller.
- `status.conditions[type=CredentialsRotation]` on the app CR, with reasons `Pending` / `Applied` / `Failed`, plus `status.observedRotation` echoing the last applied counter — the completion signal and the durable baseline in one place. While a rotation is in progress the `Pending` condition names the users still outstanding (D3), so a partial apply is observable rather than hidden behind a single scalar.
- No change to how a tenant reads a password: still `kubectl get secret <release>-credentials`. The `<release>-credentials-pending` staging Secret (D3) is a controller-internal object, not part of the tenant-facing contract.

## Upgrade and rollback compatibility

Existing clusters keep working: through Release A the chart still renders the Secret (now pinned with `resource-policy: keep`) and the controller only records the baseline, so the live credential is unchanged; at Release B the object is orphaned, not deleted, and the controller takes over `data` without regenerating it (D2), so there is never a window where the Secret is absent and the live password does not change on upgrade. Migration is automatic (controller reconcile), no tenant action.

Rollback is asymmetric across the Release-B boundary, and the design makes that a stated, guarded fact rather than a lurking conflict:

- **Before Release B** (chart still renders the Secret) rollback is ordinary Helm rollback; the controller has not taken `data`.
- **After Release B** a chart-only rollback to a version that renders `<release>-credentials` is **not supported** and must be blocked, because Helm would re-take `managedFields` ownership of `data` and overwrite the live password with the stale chart value while the database holds the rotated one — a silent authentication outage. This is why Release B is a one-way migration whose recovery actor is the controller, not the chart. The supported way back is to roll the *controller* back (it re-asserts `data` from its baseline and re-runs the apply-Job to reconverge Secret and database), or, if the chart must be reverted, to follow the runbook that re-runs a forced re-apply (D5) afterwards so the database and the re-rendered Secret are brought back into agreement. The Release-B upgrade notes state this boundary explicitly and, where the delivery mechanism allows, a pre-check refuses a chart downgrade across it.

## Security

New trust boundary: a central controller that can read and write the credentials Secret in every tenant namespace and create Jobs there — by construction it can reach every managed database credential, which nothing aggregates today. Mitigated by per-tenant-namespace RBAC, a dedicated ServiceAccount, and egress restriction. Rotation revokes at the database level (new password set) and overwrites the Secret; it removes the credential's retention through Helm's `MaxHistory` revisions. It does **not** remove the live Secret from etcd or etcd backups, and it does not terminate existing sessions — a leaked password keeps working on an open connection until it closes, so a full leak response also needs a session kill (`pg_terminate_backend` / `KILL`). Account coverage is per-engine: the Postgres superuser is CNPG-managed and out of scope; MariaDB `root` is in scope via D4.

## Failure and edge cases

- Apply-Job fails partway → users already applied are committed to the live Secret and match the database; users not yet applied keep their previous, still-valid password in both places (D3, apply-before-advertise + per-user commit); condition is `Failed` with the Job's reason and the outstanding user set; `observedRotation` is not advanced; retried on the next reconcile from the same staged passwords.
- Database unreachable at rotation time → the Job blocks on readiness (as the init-Job does today) with the new passwords only in the staging Secret; the live `<release>-credentials` still advertises the old, still-valid password, so there is no divergence window; condition stays `Pending` until the Job succeeds or its `activeDeadlineSeconds` fails it.
- Apply-Job lifecycle → `activeDeadlineSeconds`, `backoffLimit`, and `ttlSecondsAfterFinished` are set so a blocked or failed Job (which mounts a privileged login and the staging Secret, D4) is bounded and reaped rather than accumulating.
- Rotation requested during a restore → the restore re-apply path runs first, then the counter apply; both use the same Job and condition; single-flight (D9) means one Job, not two.
- Controller restart mid-migration → adoption is idempotent because it keys off Helm's own state: before Release B the object is Helm-owned and the controller only baselines it; after Release B it is orphaned (`resource-policy: keep`) and the take-over of owner/labels/`data` is a no-op once already done. There is no annotation-stripping step to re-run.
- A leftover `spec.users[].password` in values → still ignored (the admission warning from #4078 stays); rotation operates only on the generated Secret. Because the chart never renders `<release>-credentials` after Release B, editing that field cannot make Helm rewrite the Secret.

## Testing

- envtest for the controller reconcile: baseline-then-adopt across the Release-A/Release-B boundary (Helm-owned → orphaned via `resource-policy: keep` → controller-owned, `data` unchanged throughout), rotation on a counter bump, restore-detect force-reapply, partial-apply → per-user condition, and a superseding bump while a Job is in flight (single-flight, D9).
- A per-operator check that a referenced-but-absent credential Secret **blocks** bootstrap rather than erroring the resource into a terminal state (D7); if it does not, the engine gets an explicit init-gate.
- Unit tests for the per-engine apply-Job rendering (Postgres `ALTER ROLE` as the CNPG superuser, MariaDB `ALTER USER` including root last), and that the live Secret is written only after each `ALTER` succeeds (apply-before-advertise, D3).
- e2e on a live release: bump the counter, assert the old password stops authenticating and the new one works, and that the live Secret never advertises a value the database rejects during the window; run a restore into a copy and assert the app-user and root logins work against the copy; attempt a chart-only rollback across Release B and assert it is refused (or, per the runbook, that the forced re-apply reconverges).

## Rollout

1. **Release A.** Ship `password-rotation-controller` (`spec.passwordRotation` served and reconciled). The chart stamps `helm.sh/resource-policy: keep` on the still-rendered `<release>-credentials` and continues to render it. The controller only baselines the existing passwords; Helm remains the sole writer of `data`, so there is no field contention (D2).
2. **Release B.** Stop the charts from rendering `<release>-credentials`; because of `resource-policy: keep` the object is orphaned rather than pruned, and the controller adopts it and becomes sole owner. In the same release, render the MariaDB `User` CR without `spec.passwordSecretKeyRef` (D8) so the operator leaves passwords to the Job. This release is a one-way migration: a chart-only rollback across it is unsupported and, where the mechanism allows, refused by a pre-check; recovery is via the controller (see Upgrade and rollback).

## Open questions

- **D3**: ephemeral apply-Job (proposed) vs the controller holding a direct DB connection. The Job keeps privilege ephemeral at the cost of per-rotation Job orchestration.
- **D5 (MariaDB restore)**: root re-apply after a restore-into-copy needs the *source* root password (the recovered datadir carries it, the target's fresh `rootPasswordSecretKeyRef` was never applied). This assumes the restore path exposes the source privileged credential to the controller. Confirm that the backup driver makes it available for both engines; where it does not, MariaDB root re-apply after restore is out of scope for that path and reported on the condition rather than silently diverging.
- **D6**: per-tenant-namespace scoping (proposed) vs a cluster-wide grant; and whether the residual "one component can reach every managed credential" trade-off is acceptable or must be scoped further. The apply-Job's privileged login (D4) also mounts a credential into the tenant namespace — for Postgres the CNPG superuser, which is otherwise outside the tenant-facing set; confirm this is acceptable given the tenant's existing access to secrets in its own namespace, or scope the Job's mount further.
- Returning `spec.passwordRotation` to the app API is subject to the same API-owner gate as the rest of this epic.

## Alternatives considered

- **Keep rotation in the chart (the #4078 approach).** Rejected: a chart-rendered Secret's cleartext is retained through Helm's `MaxHistory` revisions, so the bump cannot revoke; this is Helm behaviour a template cannot work around, and it is the reason for this proposal.
- **Controller holds a persistent superuser/root connection to each database.** Rejected as the default: simpler to write, but gives the controller a standing high-privilege connection to every managed database on top of the credential access; the ephemeral apply-Job (D3) keeps DB access short-lived.
- **A single apply mechanism reusing each operator's own path (Postgres init-Job, MariaDB operator User CR).** Rejected: it cannot produce a uniform completion signal, leaves MariaDB root uncovered, and for MariaDB non-root creates two writers (operator + rotation) that conflict.
