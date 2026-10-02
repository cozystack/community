# 0001. Decision records live with the proposal they amend

- **Number:** `0001`
- **Date:** `2026-08-20`
- **Status:** Accepted
- **Deciders:** `@myasnikovdaniil, @lllamnyp`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** [`cozystack/community#56`](https://github.com/cozystack/community/pull/56)
- **Implemented in:** [`cozystack/community#56`](https://github.com/cozystack/community/pull/56)

## Context

Decision records were first proposed as a root-level `decisions/` directory with a single global sequence — `decisions/0001-…`, `decisions/0002-…` — following Michael Nygard's convention and the layout `adr-tools` produces. That shape was written, reviewed and complete before the placement question was raised in review of [#56](https://github.com/cozystack/community/pull/56).

The objection that reopened it came from the proposal's own template. The `Proposal:` field read `design-proposals/<name>/README.md` — **or `none`**, which presupposes that a decision can exist in this repository without a proposal. The repository's front-door table routes every candidate somewhere: a cross-cutting architectural change to a design proposal here, a bug or scoped feature to `cozystack/cozystack`, governance to an issue here. There is no residue. A deliberation weighty enough to need a record, with no proposal to attach it to, has demonstrated that it is a proposal.

At the time of the decision the repository held five design proposals and routinely had five or so pull requests open at once.

## Decision

Records live under the proposal they amend, at `design-proposals/<proposal>/decisions/NNNN-slug.md`, numbered per proposal. The process text lives in `design-proposals/README.md` and the template at `design-proposals/decision-template.md`. There is no root-level `decisions/` tree and no global sequence, and the root README continues to route two document classes rather than three.

## Why not the alternatives

- **A root-level `decisions/` log with a global monotonic sequence.** The case for it is real and giving it up costs something. (a) A monotonic id is short, citable and survives a directory rename — `ADR 0007` is a durable reference in a way `compute-plane ADR 0002` is not, and external citations are exactly what a global id serves. (b) Nygard's convention and its tooling assume one log; `adr-tools` defaults to a single `doc/adr`, and choosing otherwise forfeits it. (c) "What did we decide lately?" is a real question a single directory answers by listing itself. (d) Decisions that span several proposals have no single home under per-proposal placement, and this nearly carried the argument. What decided it against: (a) is answered by accepting a more verbose but more informative citation; (b) by the fact that nothing in this repository would consume that tooling — there is no CI here at all, and the template already departs from Nygard's four sections; (c) by `git log -- 'design-proposals/*/decisions/[0-9]*.md'`, which answers it in any layout and better than directory order does, since it carries dates and authors; and (d) by scoping cross-proposal *principles* out of this log entirely rather than letting the log absorb them — see the proposal's open question. Against all four stands the concrete cost of the global sequence: with five pull requests routinely open, a global number collides constantly, and "whoever merges second renumbers on rebase" invalidates any citation already written down. Per-proposal numbering collides only when two pull requests touch the same proposal.
- **Freezing merged proposals instead, and superseding them — the Rust RFC and Python PEP model.** [Rust](https://github.com/rust-lang/rfcs) holds that "once accepted, RFCs should not be substantially changed"; [PEP 1](https://peps.python.org/pep-0001/) that "PEPs are no longer substantially modified after they have reached the Accepted, Final, Rejected or Superseded state". Under that model supersession is the only way to change anything, so the history is automatic and no second document class is needed. Rejected here because it contradicts this repository's existing position that merged proposals are "a reference, not a binding spec" and that the codebase is the source of truth, and because it would turn every implementation finding into a new proposal — a much heavier governance change than the one under discussion. It is the coherent alternative rather than a weak one, and it is the model the largest peer processes actually chose; [Kubernetes](https://github.com/kubernetes/enhancements/blob/master/keps/sig-architecture/0000-kep-process/README.md), which keeps living proposals as this repository does, has the same gap and fills it only with milestone dates.
- **Records in the product repository, next to the code.** Rejected: the decisions in scope are decisions about proposals, and the proposals are here. A decision whose subject is a fact about code has its primary home in a comment and a test at that site, which is now a rule rather than an alternative.
- **A separate repository for decisions.** Rejected as a third place to look for the same argument, for a repository that currently holds five proposals.

## Consequences

- Both link directions come for free: a reader browsing a proposal's directory sees its decisions, and a record's parent directory is its proposal. Nothing to maintain, and hand-maintained backlinks are the first thing to rot. The `Decisions` section in the proposal template becomes a convenience rather than the only path between the two.
- Citations become longer and qualified — `compute-plane ADR 0002` rather than `ADR 0014`. External references to a record are correspondingly more fragile if a proposal directory is ever renamed.
- A withdrawn proposal takes its decisions with it instead of orphaning entries in a global log.
- Cross-proposal standing constraints are now explicitly homeless, which is the honest outcome: they were never served by either layout, and this decision refuses to pretend otherwise by filing them under an arbitrary proposal. Finding them a home is tracked as an open question against `cozystack/cozystack`.
- Restructuring cost was paid once, while the log had one entry. It would not have stayed cheap: the review's argument for deciding now rather than later was that the move stops being mechanical once citations point at the global ids from other repositories.

## Revisit if

Cross-proposal decisions become common enough that scoping them out stops being tenable, or a principles document lands in `cozystack/cozystack` and turns out to be the natural home for records that bind more than one proposal. Also revisit if records are not being written in practice — that would make the frozen-proposal alternative the live option rather than this one.
