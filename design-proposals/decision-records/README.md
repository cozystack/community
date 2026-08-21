# Decision records

- **Title:** `Record architectural decisions alongside the proposals they amend`
- **Author(s):** `@myasnikovdaniil`
- **Date:** `2026-08-20`
- **Status:** Review

## Overview

This proposal introduces **decision records** to this repository: short, dated notes on architectural decisions the project has already made, and why — stored under the design proposal each one amends.

A design proposal is intent, written before the work. When implementation contradicts it we revise the proposal in place, so the proposal ends up reading as though it always said the current thing, and the reasoning that changed our minds survives only in a pull-request diff. That reasoning is the most expensive thing we produce and the easiest to lose. This proposal gives it a home.

## Scope and related proposals

The operative rules — when a record is required, the template, the numbering, the immutability rule, the review bar — live in [`design-proposals/README.md`](../README.md#decision-records) and [`design-proposals/decision-template.md`](../decision-template.md), not here. A merged proposal is "a reference, not a binding spec", so this document states the change and the rationale; the process text is the law.

## Decisions

- [0001. Decision records live with the proposal they amend](./decisions/0001-decision-records-live-with-their-proposals.md) — why records are per-proposal rather than a root-level log.

## Context

### The problem

Proposal drift is not hypothetical here; it is the normal case, and it is already happening in a way that loses reasoning:

- [#42](https://github.com/cozystack/community/pull/42) and [#44](https://github.com/cozystack/community/pull/44) rewrote the database-horizontal-autoscaling proposal after an implementation spike disproved its load-bearing premise — that the autoscaler could be the enforced single owner of the application's `replicas` value.
- [#53](https://github.com/cozystack/community/pull/53) then reopened that proposal's actuation mechanism on a live CloudNativePG finding: the `Cluster` `/scale` subresource exposes no `status.selector`, and upstream [cloudnative-pg#7923](https://github.com/cloudnative-pg/cloudnative-pg/issues/7923) is closed as not planned.
- [#40](https://github.com/cozystack/community/pull/40), [#41](https://github.com/cozystack/community/pull/41) and [#36](https://github.com/cozystack/community/pull/36) are the same shape: an implementation finding rewriting an accepted proposal, with the *why* left in the pull-request body.
- The compute-plane proposal carried a `Revision (this PR):` paragraph in its metadata block because that rationale had nowhere else to live. "This PR" stops resolving the moment the next revision lands.

Three improvised solutions to one problem already exist in this repository — that metadata paragraph, the database-autoscaler's `Why this changed` section, and its spike appendix. None of them is protected from the next in-place edit.

### Prior art

The projects with the largest proposal processes split two ways on this, and the split is instructive.

| Project | Merged proposal | How superseded reasoning survives |
|---|---|---|
| [Rust RFCs](https://github.com/rust-lang/rfcs) | Frozen — "once accepted, RFCs should not be substantially changed… More substantial changes should be new RFCs, with a note added to the original RFC" | A new RFC, plus a note on the original |
| [Python PEPs](https://peps.python.org/pep-0001/) | Frozen — "PEPs are no longer substantially modified after they have reached the Accepted, Final, Rejected or Superseded state" | `Replaces` / `Superseded-By` headers, `Resolution` link |
| [Kubernetes KEPs](https://github.com/kubernetes/enhancements/blob/master/keps/sig-architecture/0000-kep-process/README.md) | Living, edited per release | `superseded-by` / `replaces` metadata and an `Implementation History` section — which records **milestones and dates, not reasoning** |

Cozystack is in the Kubernetes camp by explicit choice: proposals here are a reference rather than a binding spec, and the codebase is the source of truth. That choice is right for this project, and it is also exactly why the gap exists — Rust and Python do not need decision records because freezing the proposal makes supersession the only way to change anything, so the history is automatic.

Projects that keep living proposals and want the reasoning tend to add a second, immutable document class: [Backstage](https://backstage.io/docs/architecture-decisions/) keeps an ADR log in its docs tree, and the [GOV.UK Design System](https://github.com/alphagov/govuk-design-system-architecture/blob/main/proposals/001-use-rfcs-and-adrs-to-discuss-proposals-and-record-decisions.md) adopted RFCs and ADRs together, dividing them exactly this way — the RFC explores options, the ADR records what was decided.

## Goals

- The reasoning behind a design change is recoverable a year later without reading a pull-request thread.
- A record is cheap enough to write that it actually gets written: one page, one reviewer, no new front-door concept.
- The record cannot be quietly rewritten into agreement with the present.
- Where code can enforce a decision, the code stays the primary record and the note does not duplicate it.

### Non-goals

- Not a second design document. A record that grows into one has failed.
- Not a changelog, and not user documentation.
- Not a governance process for making decisions — only for recording ones already made.
- Not retroactive. Backfilling every past decision is not proposed; records start from the ones being made now, plus [0001](../compute-plane/decisions/0001-computeplane-ships-as-an-operator-owned-module.md) as a worked example.

## Design

A record is a one-page markdown file at `design-proposals/<proposal>/decisions/NNNN-slug.md`, numbered per proposal, with a maintained header block and frozen prose. It carries Context, Decision, Why not the alternatives, Consequences, and Revisit if.

Two rules carry the design:

1. **The prose is frozen after merge; the header is maintained.** `Status`, `Superseded by` and `Implemented in` must track reality or the lifecycle breaks; the body must not, or the record becomes just another document that agrees with the present.
2. **Where a decision is a fact about code, the code is the primary record.** A comment at the site plus a test that fails when the invariant is violated, and the record links to it. Cozystack already does this better than prose does — the `computeplane` release-name guard and its test state the mechanism more precisely than the first draft of record 0001 managed to.

The full rules are in [`design-proposals/README.md`](../README.md#decision-records).

## User-facing changes

None for users of Cozystack. For contributors: one new optional document type, one new template, and a pull-request checkbox asking whether a proposal-revising change needs a record.

## Upgrade and rollback compatibility

Not applicable — no code, no API, no cluster state. Rolling this back means deleting the template and the process section; existing records stay readable as ordinary markdown.

## Security

No new trust boundary, tenant input, RBAC surface or secret. Records are public documents in a public repository and must not carry cluster-identifying or client-identifying detail.

## Failure and edge cases

- **A record is written for a decision that was not actually settled** → review turns into re-litigating it. That is the signal; close the pull request and open a proposal or an issue instead.
- **Two pull requests claim the same number under one proposal** → whoever merges second renumbers on rebase. Per-proposal numbering makes this rare rather than constant.
- **The decision spans several proposals** → file it under the one it changes most and link it from the others; if it belongs to no proposal, it is a proposal.
- **The decision is a standing constraint on the platform rather than on one proposal** → out of scope here. See [Open questions](#open-questions).
- **A proposal is withdrawn** → its records go with it, which is correct; nothing is orphaned.
- **Nobody writes records** → the failure mode Backstage names in its own ADR001, and the reason enforcement is a reviewer checkbox rather than a convention.

## Testing

There is no CI in this repository — `.github/` contains issue templates only — so there is nothing to assert mechanically. Validation is that the next proposal-revising pull request either adds a record or says why it does not, and that the reviewer checkbox is the place that gets noticed.

## Rollout

1. This proposal, the process text, and the template land together.
2. [0001 under compute-plane](../compute-plane/decisions/0001-computeplane-ships-as-an-operator-owned-module.md) ships as a backfilled worked example, and [0001 under this proposal](./decisions/0001-decision-records-live-with-their-proposals.md) as one written by its own author from an argument they were present for.
3. A second record from a different subsystem — the database-horizontal-autoscaling rev1 rejection — is requested on [#53](https://github.com/cozystack/community/pull/53) from the contributor who owns that argument, rather than backfilled here by someone who does not.
4. The improvised in-proposal narratives are extracted as their proposals are next revised. Compute-plane's `Revision (this PR):` field is retired here, since this change already touches that proposal; the database-autoscaler's `Why this changed` section and spike appendix go with the record requested on [#53](https://github.com/cozystack/community/pull/53).

## Open questions

- **Where does a design principle live?** Some constraints surfaced by a decision are not about that proposal at all — that `ApplicationDefinition` has no operator-fixed-values facility, and that settability and defaultability are the same property in a structural schema, bind every operator-owned module anyone builds next. A record filed under one proposal is the wrong home, and there is no principles document in `cozystack/cozystack` today. Tracked separately so this log does not absorb it by default.
- **Does the lighter review bar need explicit maintainer agreement?** One approval for a record, against consensus for a proposal, is a governance change and is called out as one rather than merged as documentation.

## Alternatives considered

- **A root-level `decisions/` log with a global sequence.** The original shape of this proposal. Rejected; the argument is recorded in [0001](./decisions/0001-decision-records-live-with-their-proposals.md).
- **Freeze merged proposals instead, Rust/PEP style.** Supersession then produces the history for free and no second document class is needed. Rejected as a heavier governance change that contradicts the existing "reference, not a binding spec" position and would make every implementation finding a new proposal — but it is the option the largest peers picked, and it remains the coherent alternative if records are not written in practice.
- **Leave the reasoning in pull-request bodies and rely on search.** This is the status quo, and the six pull requests in Context are what it produces: reasoning that is technically present and practically unfindable, attached to a diff rather than to the design.
- **Put the reasoning in the proposal itself, in a revision-history section.** Already tried three times here, in three different shapes, none protected from the next in-place edit. It also makes the proposal longer exactly where a reader wants it shorter.
- **Record decisions only in code comments and tests.** Correct for anything code can enforce, and now a rule rather than an alternative. It does not cover decisions about what *not* to build, or ones whose subject is a constraint rather than a line of code.
