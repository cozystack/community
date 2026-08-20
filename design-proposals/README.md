# Cozystack Design Proposals

This folder contains design proposals for features and architectural decisions that impact Cozystack as a whole.

## Purpose

Design proposals allow community members to share their ideas early, get feedback, and build consensus before implementation begins.
The process is especially useful for:

- Architectural decisions that will shape multiple features in the future
- Features that require choosing between competing architectural approaches with tradeoffs
- Decisions that affect multiple components or APIs

By proposing designs up front, we aim to reduce risk and ensure the project evolves in a coordinated, community-driven way.

**Note:** Using the design proposal process is optional, but strongly encouraged—especially for non-trivial or cross-cutting work.

## How to Create a Proposal

1.  Make a new directory under `./design-proposals` and create a README.md file in it:

    ```
    ./design-proposals/<proposal-title>/README.md
    ```
    
    Describe your proposal in this file.
    
    Put all additional materials, such as diagrams, reference docs, and others, in the same folder.
    Note that for diagrams you can use [Mermaid.js](https://mermaid.js.org/) markup, which is natively rendered on GitHub
    directly in the Markdown files.
    
1.  Remember to include basic metadata at the top:

    - Title
    - Author(s)
    - Date
    
1.  As with all commits in CNCF projects, please sign the commit for a DCO check:
    
    ```bash
    git commit --signoff 
    ```
    
1.  Submit a pull request with your proposal and request feedback.

To bring attention to your proposal, share it in the Cozystack community:

-   [Community chat in Telegram](https://t.me/cozystack)
-   [CNCF Slack – #cozystack](https://cloud-native.slack.com/archives/C08BQJD95J7)
-   [Kubernetes Slack – #cozystack](https://kubernetes.slack.com/archives/C06L3CPRVN1)
-   Bi-weekly Cozystack community calls

## Approval Process

Proposals are reviewed in GitHub PRs. Once a proposal receives consensus from core maintainers (via `/lgtm` and comments), it will be merged and considered accepted. Merged proposals serve as a reference, not a binding spec.

## Proposal Drift

We understand that real-world implementation may diverge from initial designs. This is normal and expected.

Update the proposal when the divergence is significant. When the divergence came from a decision worth remembering — an approach that failed, a constraint you discovered, an alternative you picked instead — also write a [decision record](#decision-records) under that proposal and link it from the proposal's `Decisions` section. Editing the proposal alone loses the reasoning: the revised text reads as though it always said the current thing, and *why* the design changed course survives only in the pull-request diff.

The **codebase and user documentation remain the final source of truth** for what the system does. A decision record is the source of truth for why it is that way.

## Decision Records

A design proposal is intent — what we think we should build, written before the work. A decision record is history — what we settled on, written once the question is closed. Both are useful; the second is the one we were missing.

Records live **with the proposal they amend**, numbered per proposal:

```
./design-proposals/<proposal-title>/decisions/NNNN-short-slug.md
```

A decision is a decision *with respect to* something, and here that something is always a proposal. The [front-door table](../README.md) routes every other candidate elsewhere — a bug or scoped feature to [cozystack/cozystack](https://github.com/cozystack/cozystack/issues/new/choose), governance to an issue here — so a deliberation weighty enough to need a record, with no proposal to attach it to, has just demonstrated that it *is* a proposal. Write that instead.

Placing records under their proposal means both link directions come for free, numbering collides only when two pull requests touch the same proposal, and a withdrawn proposal takes its decisions with it instead of orphaning entries in a global log.

|  | Design proposal | Decision record |
|---|---|---|
| Written | before the work, to get agreement | once the question is settled |
| Answers | "should we, and how might we?" | "what did we decide, and why not the alternatives?" |
| Edited after merge | yes, as the design evolves | never — superseded by a new record |
| Length | as long as it needs to be | one page |
| Review | consensus from maintainers | one maintainer checks it for accuracy |

Both have an alternatives section, and they are not the same list. A proposal weighs the options we could imagine *before* building. A record names the option that lost *during* building — frequently the proposal's own original design.

### When to write one

Write a decision record when a future contributor would otherwise have to reconstruct the reasoning from a pull-request thread. In practice:

- Implementation contradicted an accepted proposal and the design changed course.
- Two viable approaches existed and we picked one for reasons that are not visible in the resulting code.
- We hit a constraint that now shapes the design — an upstream limitation, a Kubernetes semantic, a vulnerability class.
- We deliberately decided *not* to do something, and the question keeps coming back.

Do not write one for:

- Routine code choices that the diff and the tests already explain.
- Operator-facing how-to — that is user documentation, and it belongs on [the website](https://cozystack.io/docs/).
- The mechanics of the product repository's own workflow (release process, changelog conventions) — those live next to the code in [cozystack/cozystack](https://github.com/cozystack/cozystack).
- A decision that has not been made yet. That is a design proposal, or an open question inside one.
- A contract that code can enforce. See [What belongs in code instead](#what-belongs-in-code-instead).

### How to write one

1.  Copy [`decision-template.md`](./decision-template.md) to the proposal's `decisions/` directory, taking the next free number *for that proposal*:

    ```
    ./design-proposals/compute-plane/decisions/0002-short-slug.md
    ```

1.  **Title the decision, not the topic.** `storageClass is immutable after creation`, not `storageClass immutability`. Someone scanning the directory should learn what we decided from the filename alone.

1.  **Keep it to a page.** Link out to the proposal, the code and the pull requests for detail. A record that grows into a second design document will not get read.

1.  **Link the pull request where the decision was argued** in `Decided in:`, and **source every rejected alternative** to the comment or pull request it came from. A record is checkable for accuracy only if its claims are traceable; alternatives written from memory are where inaccuracy gets in.

1.  **Link it from the proposal's `Decisions` section**, newest first.

1.  As with all commits in CNCF projects, sign the commit for the DCO check:

    ```bash
    git commit --signoff
    ```

To see the records across all proposals, newest first:

```bash
git log --diff-filter=A --date=short --format='%ad %an' --name-only \
  -- 'design-proposals/*/decisions/[0-9]*.md'
```

### What belongs in code instead

Where a decision's content is a fact about a type, a field or an invariant that code must respect, its primary home is **a comment at that site plus a test that fails when the invariant is violated**. The record links to that site rather than restating it.

Cozystack already does this well. The `computeplane` release-name invariant is enforced at `packages/extra/computeplane/templates/check-release-name.yaml` and pinned by `packages/extra/computeplane/tests/release_name_test.yaml`, whose suite comment carries the mechanism — the aggregated API rebuilds HelmRelease specs without `spec.releaseName`, so the release name is always the object name. That guard is a more reliable record than any prose, and it stays more precise, because a test fails when it goes stale and a paragraph does not.

Without this rule the log fills with restatements of things the code already enforces.

### Status and immutability

| Status | Meaning |
|---|---|
| `Accepted` | Current. The decision stands. |
| `Superseded by NNNN` | A later record replaced it. The original text stays exactly as written. |
| `Reverted` | We undid it. Say what we do instead. |

The header block and the body have different rules, and the distinction matters:

- **The header block is maintained.** `Status`, `Superseded by` and `Implemented in` must track reality — the lifecycle depends on it, and a stale `Implemented in: not yet` makes the record actively misleading.
- **The prose below it is frozen.** Once merged, the body of an accepted record is not edited. Fix typos and broken links; change nothing else. If the decision changes, write a new record and mark the old one `Superseded by NNNN`.

That second rule is what makes these records a history instead of a second set of documents to keep current. A record that gets quietly rewritten is worth no more than the proposal it was meant to supplement.

### Review

A decision record documents a decision that has already been made. Review therefore checks that the record is **accurate** — not whether the reviewer agrees with it. One maintainer's approval is enough, and it should be quick.

This is a deliberately lighter bar than the consensus a design proposal needs, and it applies *only* to records. If review turns into re-litigating the decision, that is a signal the decision was not actually settled. Close the pull request and open a design proposal or an issue instead.

## Inspiration

This process is inspired by [KubeVirt's design proposals](https://github.com/kubevirt/community/tree/main/design-proposals) and [Kubernetes Enhancement Proposals](https://github.com/kubernetes/enhancements).

The decision-record format follows [Michael Nygard's architecture decision records](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) and [MADR](https://adr.github.io/madr/), trimmed to the sections we will actually fill in.
