# Cozystack Decision Records

This folder holds **decision records**: short, dated notes on architectural decisions the project has actually made, and why.

A [design proposal](../design-proposals/README.md) is intent — what we think we should build, written before the work. A decision record is history — what we settled on, written once the question is closed. Both are useful; the second is the one we have been missing.

## Why a separate folder

Design proposals get edited. When implementation contradicts the design — and it regularly does — the proposal is revised in place, so it ends up reading as though it always said the current thing. The reasoning that changed our minds (*we tried X, hit a concrete constraint, moved to Y*) then survives only in a pull-request diff that nobody will find in a year.

That reasoning is the most expensive thing we produce and the easiest to lose. A decision record is where it goes.

|  | Design proposal | Decision record |
|---|---|---|
| Written | before the work, to get agreement | once the question is settled |
| Answers | "should we, and how might we?" | "what did we decide, and why not the alternatives?" |
| Edited after merge | yes, as the design evolves | never — superseded by a new record |
| Length | as long as it needs to be | one page |
| Review | consensus from maintainers | one maintainer checks it for accuracy |

Both have an alternatives section, and they are not the same list. A proposal weighs the options we could imagine *before* building. A record names the option that lost *during* building — frequently the proposal's own original design.

## When to write one

Write a decision record when a future contributor would otherwise have to reconstruct the reasoning from a pull-request thread. In practice:

- Implementation contradicted an accepted proposal and the design changed course.
- Two viable approaches existed and we picked one for reasons that are not visible in the resulting code.
- We hit a constraint that now shapes the design — an upstream limitation, a Kubernetes semantic, a vulnerability class.
- The code enforces a contract it cannot explain: a field is immutable, a release name is load-bearing, an ordering is required.
- We deliberately decided *not* to do something, and the question keeps coming back.

Do not write one for:

- Routine code choices that the diff and the tests already explain.
- Operator-facing how-to — that is user documentation, and it belongs on [the website](https://cozystack.io/docs/).
- The mechanics of the product repository's own workflow (release process, changelog conventions) — those live next to the code in [cozystack/cozystack](https://github.com/cozystack/cozystack).
- A decision that has not been made yet. That is a design proposal, or an open question inside one.

## How to write one

1.  Copy [`template.md`](./template.md) to `decisions/NNNN-short-slug.md`, taking the next free number:

    ```
    ./decisions/0007-etcd-is-per-cluster-not-per-tenant.md
    ```

1.  **Title the decision, not the topic.** `storageClass is immutable after creation`, not `storageClass immutability`. Someone scanning the folder should learn what we decided from the filename alone.

1.  **Keep it to a page.** Link out to the proposal, the code and the pull requests for detail. A record that grows into a second design document will not get read.

1.  **Link both ways.** The record links its proposal and the pull requests that implemented it; the proposal's `Decisions` section links back to the record.

1.  As with all commits in CNCF projects, sign the commit for the DCO check:

    ```bash
    git commit --signoff
    ```

Two pull requests can claim the same number. Whoever merges second renumbers on rebase — a file rename and a couple of link fixes.

## Status and immutability

| Status | Meaning |
|---|---|
| `Accepted` | Current. The decision stands. |
| `Superseded by NNNN` | A later record replaced it. The original text stays exactly as written. |
| `Reverted` | We undid it. Say what we do instead. |

Once merged, **the body of an accepted record is not edited.** Fix typos and broken links; change nothing else. If the decision changes, write a new record and mark the old one `Superseded by NNNN`.

This one rule is what makes the folder a history instead of a second set of documents to keep current. A record that gets quietly rewritten is worth no more than the proposal it was meant to supplement.

## Review

A decision record documents a decision that has already been made. Review therefore checks that the record is **accurate** — not whether the reviewer agrees with it. One maintainer's approval is enough, and it should be quick.

If review turns into re-litigating the decision, that is a signal the decision was not actually settled. Close the pull request and open a design proposal or an issue instead.

## Where this sits relative to everything else

| The question you are answering | Where it is answered |
|---|---|
| "Why is it built this way, and not the obvious other way?" | A decision record, here |
| "What should we build?" | A [design proposal](../design-proposals/README.md), here |
| "How do I use it?" | [User documentation](https://cozystack.io/docs/) on the website |
| "How does this repository release, or write changelogs?" | [cozystack/cozystack](https://github.com/cozystack/cozystack), next to the code |

## Inspiration

The format follows [Michael Nygard's architecture decision records](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions) and [MADR](https://adr.github.io/madr/), trimmed to the sections we will actually fill in.
