# Platform design principles

- **Title:** `Write down the platform's design principles, and the layer that enforces each one`
- **Author(s):** `@lllamnyp`

## Overview

Cozystack's architectural convictions live in maintainers' heads and in review threads, not in any document a contributor reads before writing code. Most changes are now written and reviewed with AI agents, and an agent knows only what `AGENTS.md` and the documents it routes to say. Where those documents are silent, an agent follows the nearest written rule or copies the nearest precedent, and in two places the written rules point the opposite way from what maintainers expect.

This proposal writes down six principles: Cozystack is a framework, so the platform does not name applications; a test reaches code through its interface; applications are tested where they live; a rule is written down before CI enforces it; an API is more than its schema; and a review checks the design, not only the text. For each it names the layer that enforces it. The normative text goes into `cozystack/cozystack`, where agents and contributors read it; this proposal carries the reasoning, and changing a principle means amending it.

## Scope and related proposals

- [Decision records](../decision-records/README.md) leaves open where a design principle lives, since "there is no principles document in `cozystack/cozystack` today". This proposal answers that question for the principles below.
- [Tenant quotas as reservation limits](../tenant-quota-reservation/README.md) is the case study in [Context](#context). Its Testing section prescribes tests this proposal rules out; [Rollout](#rollout) says how that is recorded.
- [Splitting Cozystack into the platform and catalogs (community#106)](https://github.com/cozystack/community/pull/106), a draft, needs the boundary these principles state, in particular its K1 contract for `ApplicationDefinition`. This proposal does not depend on it and does not decide the split.
- Open pull requests in `cozystack/cozystack` that cover parts of the same ground: [#5417](https://github.com/cozystack/cozystack/pull/5417) asks a new check to name the requirement it protects, [#5253](https://github.com/cozystack/cozystack/pull/5253) defines what counts as a breaking change, and [#5194](https://github.com/cozystack/cozystack/pull/5194) keeps application-specific helpers out of `cozy-lib`. The implementation of this proposal builds on them rather than restating them.

## Decisions

## Context

`cozystack/cozystack#5349` added a `spec.consumption` field to `ApplicationDefinition`, consumption declarations for four application kinds, and `pkg/reservation/shipped_test.go`. Every commit carries `Assisted-by: LLM`. The human review covered the commit history and the downstream checklist, and found nothing to change in the code. The test is where the gap shows:

- It lives in a platform package and names four applications. It globs `packages/system/*-rd/cozyrds/*.yaml`, regex-parses the cozy-lib preset template, and compares each declaration with about seventy figures written by hand from a reading of the charts. Nothing in it renders a chart.
- It enforces rules nobody wrote down: one definition per file, unique names across every `-rd` chart, an exact preset count, instance types that are never templated. [`cozystack#4508`](https://github.com/cozystack/cozystack/pull/4508), which selects one of two definition files with a Helm conditional, went red on it without being wrong. The platform already has a rule for that case, enforced at admission by the `ApplicationDefinition` kind validator.
- It passes while the real render is wrong: changing a chart's disk-size fallback, or bumping a preset and leaving the old row in a comment below the table, keeps it green while the chart renders different figures.
- The only review the test received was automated, and each of its suggestions added another guard. The duplicate-name check that broke `#4508` is one of them.
- The field itself is an embedded language: a `map[string]string` of CEL whose variables, functions, defaulting and error behaviour are defined only in Go. The API Review Gate requires an API owner only for a new group, a new resource or a breaking schema diff, so it reported "no sizeable API change" on `#5349` and on `#5397`, the follow-up that makes every kind without a declaration unorderable once its flag is on.

[`cozystack#5411`](https://github.com/cozystack/cozystack/pull/5411) is open to remove the test. The question this proposal answers is why an agent-assisted author and an agent-assisted reviewer both produced and accepted it, and the answer is in what the repository tells them:

- The accepted reservation design asks, in its Testing section, for a unit test "table-driven over each package's `values.yaml` defaults… asserting the exact `ResourceList` for every in-tree kind", and for one that checks "the evaluator resolves each [preset] to the same figure the chart renders". The test implements both literally.
- `docs/agents/overview.md` asks for a test first and its failure paths first, says not to delete a test, and says of two charts that must agree: "a test says so". It says nothing about what a test may depend on.
- Precedent agrees. Go tests under `internal/controller/` read files from `packages/`, one of them named `TestShippedClusterRoleNamesEveryTypeThisControllerReads`, and dozens of `hack/` scripts read chart templates directly.
- `docs/agents/contributing.md` lists the review blockers as "decided by the text alone": commit messages, trailers, comments. A review that clears them has done what the document asks.
- `AGENTS.md` routes "add a field to this CRD" to a document that says nothing about API review.
- The word "framework" appears once in the agent documents, in the sentence describing the project, with no rule attached.

### The problem

A maintainer reviewing such a change finds the problem by experience; an agent with a blank context cannot. It cannot recover a principle from a large codebase that contradicts it in places, and it will accept any test that is green and covers the feature, because the documents tell it every feature needs one. Every principle that exists only in maintainers' heads, in their private agent configuration or in a draft proposal is invisible at exactly the two points where it matters: when the code is written and when it is reviewed.

## Goals

- Every principle below has normative text in `cozystack/cozystack` that `AGENTS.md` routes to, so an agent starting from `AGENTS.md` alone reaches it before writing the code it governs.
- No written rule in `cozystack/cozystack` contradicts a principle; the sentence "when two charts have to agree, a test says so" is replaced.
- Each principle names the layer that enforces it: generation, a shared constant, validation at admission, e2e, or review.
- A design proposal's Testing section names its layers in a way that cannot prescribe a platform test over in-tree applications without a reviewer seeing it.
- Existing code that breaks a principle is declared non-precedent, so an agent does not copy it.

### Non-goals

- Removing every existing test or script that breaks a principle. They are replaced when touched or in their own changes, not by this proposal.
- Changing the API Review Gate's detection. Whether it should flag new fields on platform CRDs is an [open question](#open-questions); this proposal makes the author request the review.
- Deciding the platform and catalogs split. The framework principle holds in one repository as much as in several.
- A lint that enforces the test rules mechanically. A check that greps Go tests for `../../packages` would be exactly the kind of check principle 2 rules out.

## Design

### 1. Cozystack is a framework, not a fixed catalog

The platform has to work with an `ApplicationDefinition` it has never seen, from this repository or from a third-party catalog. Code, tests and charts outside an application's own packages do not name its kind, read its files or restate its behaviour. What the platform needs from applications is a field on the `ApplicationDefinition` and a check that runs on any definition. Places where the platform names an application today are technical debt, not precedent.

**Enforced by:** review, against the text in `AGENTS.md` Working Principles.

### 2. A test reaches code through its interface

A test exercises the code it covers through that code's interface: a Go package through its API, a chart through `helm template` or helm-unittest, a deployed component in e2e. A Go test does not open chart templates, `cozyrds/` files, `values.yaml` or other files under `packages/`, and no test greps or regex-matches source, templates or rendered manifests for a string. Such a test passes when a stray comment matches its pattern, fails when the packaging changes in a way Helm accepts, and keeps a second copy of behaviour the toolchain already defines.

When two artifacts have to agree, the agreement is made mechanical, in this order of preference:

1. one is generated from the other under `make generate`, which the codegen drift check enforces;
2. both read one constant or one data file, so a rename fails to compile or to render;
3. the component that consumes the artifact validates it when it is applied, so a mismatch fails in the cluster with a clear error;
4. an e2e suite asserts the behaviour against what the cluster actually built.

When none fits, the author says so in the pull request and a maintainer decides. Replacing a test at the wrong layer with one at the right layer is not weakening coverage.

**Enforced by:** review, against `docs/agents/overview.md`, which also says that existing tests of this kind are not precedent.

### 3. Applications are tested where they live

What the platform requires of applications is enforced by a check that ships with the platform and runs on any `ApplicationDefinition`: at admission, or as a command a catalog's CI runs on its own definitions. Whether one application's chart and declarations are right is tested in that application's package and in its e2e suite. A design proposal's Testing section does not override this.

**Enforced by:** the platform's admission checks, and review of proposals' Testing sections (see [7](#7-design-proposals-name-their-test-layers)).

### 4. A rule is written down before CI enforces it

A test or check that constrains how other packages are laid out, named or written is a contract. It lands with its rule in `docs/agents/` in the same pull request and is reviewed as a contract. One that exists only as a failing test is a defect, however correct each assertion is. `cozystack#5417` asks the author of such a check to name the requirement it protects; this principle adds that the requirement is written where the next contributor reads it.

**Enforced by:** review, and the "New checks" section `cozystack#5417` adds to the pull request template.

### 5. An API is more than its schema

These are API changes even when no schema diff shows them:

- a new field on a platform CRD, optional fields included;
- the semantics of a language a field carries: variables, functions, evaluation order, defaulting, error behaviour, the meaning of an empty value. A change to the Go code that evaluates it changes every declaration already written;
- a vocabulary other components or users read: quota keys, labels, annotations, condition types, `_cluster` and `_namespace` keys;
- a field optional now that a later change enforces. The enforcing change is the breaking one, in the sense `cozystack#5253` defines, whether it arrives as a schema change, a flag or a new default.

A pull request making one of these changes says so in its body, links the accepted design that settles the shape or states that none does, and requests a review from an API owner. The semantics of an embedded language are written in a design document beside the types, not only in Go doc comments.

**Enforced by:** the author's request and API-owner review, until the gate can see more (see [Open questions](#open-questions)).

### 6. A review checks the design

The review blockers in `docs/agents/contributing.md` are the floor of a review, not its scope. Before approving, a reviewer answers four questions: does the change follow the accepted design it implements, and does it name each deviation; does platform code or a platform test now name an application or read an application package; does it add a rule other contributors must follow, and is that rule written down; is it an API change the gate did not flag. An automated reviewer's request to harden a test is weighed against whether the test should exist at all: a test that grows a guard every round is checking the wrong layer.

**Enforced by:** review, against `docs/agents/contributing.md`.

### 7. Design proposals name their test layers

The Testing section of [`template.md`](../template.md) asks for the layer of each assertion and says that per-application correctness belongs in the application's own tests and in e2e, not in a platform unit test over in-tree applications. A reviewer of a proposal checks the Testing section against principles 2 and 3, as the implementing agent will read it as an instruction.

**Enforced by:** the template, which this proposal's pull request changes, and proposal review.

### Where the text lives

| Principle | Normative text in `cozystack/cozystack` |
| --- | --- |
| 1, 4 | `AGENTS.md`, Working Principles |
| 2, 3 | `docs/agents/overview.md`, "What a test may depend on" |
| 5 | `docs/agents/contributing.md`, "API Changes", with a route from `AGENTS.md` |
| 6 | `docs/agents/contributing.md`, "Review Expectations" |
| 7 | `design-proposals/template.md` in this repository |

An example implementation of the `cozystack/cozystack` part is open as a draft pull request, linked from this proposal's pull request. The text there is what agents read; when it and this proposal disagree, the disagreement is a bug in one of them, fixed by amending this proposal first.

## User-facing changes

None for operators or tenants. Contributors, and the agents they work with, get rules they can read before writing a change instead of learning them from a red build or a late review.

## Upgrade and rollback compatibility

Nothing is deployed. Existing tests and scripts that break principle 2 keep running; the text declares them non-precedent so they are not copied, and each is replaced on its own schedule. Rolling back is reverting the documentation.

## Security

No new trust boundary. Principle 5 has a security reading: a field that a later change makes fail-closed is a quota or admission boundary, and it is reviewed as one when it is introduced rather than when it is switched on.

## Failure and edge cases

- **A controller's test reads its own chart**, such as a test that the shipped ClusterRole names every type the controller reads → principle 2 still applies; the mechanical form is generating the RBAC from the controller's markers, which the drift check then enforces. Until that exists, the test stays and is not precedent.
- **No mechanical form exists** for an agreement between two artifacts → the author says so in the pull request, and a maintainer decides; the decision is a decision record under the proposal it concerns.
- **An accepted proposal prescribes a test the principles rule out** → the implementer raises it on the proposal or in the pull request instead of writing the test, and the outcome is a decision record under that proposal.
- **A reviewer applies the four questions mechanically** → they are questions to answer in the review, not boxes; an answer that cites nothing is not an answer.
- **The principles drift from the text in `cozystack/cozystack`** → whichever changed without the other is the bug; this proposal is amended first.

## Testing

The principles are prose and are not machine-checked, deliberately (see [Non-goals](#non-goals)). They are validated the way review rules are: the next agent-assisted pull request that adds a platform test over application packages, or an API field without an API owner's review, is caught in review by a reviewer, human or agent, who started from `AGENTS.md`. The one mechanical part is the existing codegen drift check, which principle 2 asks contributors to use.

## Rollout

1. This proposal, with the template change in principle 7.
2. The `cozystack/cozystack` text, reconciled with `#5417`, `#5253` and `#5194` as they merge.
3. A decision record under [Tenant quotas as reservation limits](../tenant-quota-reservation/README.md) recording that per-kind correctness is checked by the platform's validation and in e2e rather than by a platform unit test, once `cozystack#5411` settles that.
4. Existing tests that read application packages from platform code are replaced as their packages are next changed.

## Open questions

- **Should the API Review Gate flag a new field on a platform CRD?** It flags only new groups, resources and breaking diffs today, so principle 5 relies on the author. Flagging every new field on `ApplicationDefinition`, `PackageSource` and `Package` would catch the case in [Context](#context) mechanically, at the cost of more owner reviews.
- **Does a pull request need an `## API change` heading in its template**, or is the request for an API owner's review enough?
- **Who reviews a change to these principles?** Consensus of core maintainers, as for any proposal, is the default this proposal assumes.

## Alternatives considered

- **Keep the principles in reviewers' private agent configuration.** This is the status quo. It reaches only the reviewer who holds it, never the author's agent, and never a contributor outside the team.
- **Put the principles only in this repository.** Agents working in `cozystack/cozystack` do not read this repository unless something routes them here, and nothing does. The text has to be where `AGENTS.md` points.
- **Record each principle as a decision record under the proposal where it surfaced.** [Decision records](../decision-records/README.md) rules this out: these constraints bind every proposal, not the one they came up in.
- **Enforce the test rules with a lint.** A lint that searches Go tests for paths into `packages/` is a check that greps source, the thing principle 2 forbids, and it would pass a test that reaches the same files another way.
