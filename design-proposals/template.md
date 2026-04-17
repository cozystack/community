# Your proposal title

- **Title:** `Your full title`
- **Author(s):** `@your-github-handle, @coauthor`
- **Date:** `YYYY-MM-DD`
- **Status:** Draft | Review | Accepted | Superseded

<!-- Status transitions: Draft → Review (PR opened) → Accepted (merged)
     or Superseded (replaced by a later proposal; link it). -->

## Overview

<!-- One or two paragraphs. What are you proposing, and why, in plain words?
A reader who stops here should know what you're asking for and roughly why. -->

## Scope and related proposals

<!-- Sibling or follow-up proposals and anything deliberately deferred.
Link them by repo path or URL. If this proposal must land before or after
another, say so. Omit the section if there are no related proposals. -->

## Context

<!-- Background a reader needs to evaluate the proposal.
What does the system do today? Which files/components are relevant?
Link to them by repo path; do not assume the reader has your tab open. -->

### The problem

<!-- Concrete pain, ideally in the user's voice.
Scenarios that are broken or awkward today. -->

## Goals

<!-- What success looks like. Bullet list; each bullet should be testable. -->

### Non-goals

<!-- What this proposal explicitly does not do. Protects the scope from
review-time drift. -->

## Design

<!-- The core of the proposal. Break into numbered or titled subsections
as needed (data model, API, schema changes, template changes, controller
changes, etc.). Show before/after for shapes that are changing. Include
code blocks (YAML, gotemplate, Go) where prose would be ambiguous.
Diagrams: Mermaid.js renders on GitHub. -->

## User-facing changes

<!-- What admins / tenants / operators will see: CLI, dashboard, CRD shape,
docs entry point. If there is no user-facing change, say so. -->

## Upgrade and rollback compatibility

<!-- Will existing clusters, manifests, or APIs keep working?
Is a migration required? Automatic or manual?
What's the rollback story if the feature is reverted?
If the change is irreversible once applied, flag it here. -->

## Security

<!-- New trust boundaries, new tenant-supplied inputs, new RBAC surface,
new secrets stored or transmitted. If none, say so explicitly. -->

## Failure and edge cases

<!-- Concrete scenarios and expected behavior, one bullet each.
Examples:
- Invalid input X → chart rejects; Flux surfaces the error on status.
- Migration runs twice → idempotent; no-op on the second run. -->

## Testing

<!-- How this will be validated. Be specific about which layer
(unit / integration / e2e / manual) and what is asserted. -->

## Rollout

<!-- Ordered releases or phases. Which release ships what.
What gets deprecated in which release. -->

## Open questions

<!-- Unresolved design points you want reviewer input on.
Close these out or fold them into the body before the proposal is accepted. -->

## Alternatives considered

<!-- For each alternative, one short paragraph: what it is, why it was rejected.
Group by dimension (data model, runtime mechanism, schema approach, etc.)
when you evaluated options along multiple axes. Prevents reviewers from
re-litigating options you already dismissed. -->

---

<!--
Inspired by KubeVirt enhancement proposals
(https://github.com/kubevirt/enhancements) and Kubernetes Enhancement
Proposals (KEPs).
-->
