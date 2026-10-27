# 0001. Each original reaches the release line as a merge commit with its own commits

- **Number:** `0001`
- **Date:** `2026-10-27`
- **Status:** Accepted
- **Deciders:** `@myasnikovdaniil`
- **Proposal:** [`../README.md`](../README.md)
- **Decided in:** the pull request that adds this record
- **Implemented in:** `not yet`

## Context

Proposal as first merged put each original into batch as one commit, `git cherry-pick -x -m 1` of its merge commit on `main`. Both `main` and release branches kept one merge commit per PR with PR's own commits inside, so batch was going to be the only place where PR lost its commits.

With `-m 1` the picked commit took author and message of the merge commit. Author was whoever merged the PR on `main`, and in 63 of last 84 fix PRs merged to `main` that name was not among PR's commit authors (names compared as strings). First batch built for release-1.6 already showed it: PR by one contributor and typo fix by outside contributor both had the merger as author. Merge commit message is PR description, so `Signed-off-by` of contributors didn't reach the line either.

`backport-batch fold`, the check of a resolved original, ran only on resolver's side. Agent misled by PR text could skip it, and nothing checked the result after push.

## Decision

Batch carries each original as merge commit with all PR's commits inside, same as `main`. Batcher picks every non-merge commit of the PR in order with `cherry-pick -x` and closes them with merge commit that has message of the original merge and a trailer naming it. Commits that merged `main` into PR branch are skipped, squashed original is one plain pick. Conflict resolution has the same shape, and CI check on batch PR runs `fold` rule for every original in batch, clean picks included.

## Why not the alternatives

- One squashed commit per original (`cherry-pick -x -m 1`), the design merged in [cozystack/community#114](https://github.com/cozystack/community/pull/114). It loses authors and sign-offs as above, and line history stops matching `main`. It gives fewer conflicts, see Consequences.

## Consequences

- Every commit on line keeps its own author and `Signed-off-by`.
- Reverting one original after batch merged is still one command, `git revert -m 1` of its merge commit on line.
- More conflicts. Intermediate commit can fail to apply even when PR's final diff would apply, and one failed commit sends whole original to resolver or to next batch. Of last 84 fix PRs on `main`, 41 had more than one commit, 226 commits in total, 25 in the biggest one.
- Intermediate commits bring review fix-ups onto the line, as they are on `main`.
- Batch PR gets one more CI check, and it checks clean picks too.

## Revisit if

Conflicts in intermediate commits start deferring originals often enough to delay fixes, while final diff of those PRs would pick cleanly.
