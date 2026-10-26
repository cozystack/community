# Automatic backports

- **Title:** `Automatic backports to release lines`
- **Author(s):** `@myasnikovdaniil`

## Overview

Backports stop being a per-PR decision. Everything that can go to patch release goes there by rule: twice a week a batcher picks fixes merged to `main` onto each supported release line, newest line first, as one PR per line. Nobody requests backports, labels for them or opens backport PRs by hand. Since rules read PR title, title and content of original PR are checked at review of that PR, not at backport time.

## Decisions

## Context

Today maintainer labels original PR with `kind/backport` (newest line) or `kind/backport-previous` (previous line), `backport.yaml` opens one PR per original, conflicts are ported by hand, and `backport-audit` checks before rc that nothing labelled is missing. Numbers below are from v1.6.1 to v1.6.5: 103 originals, 90 of them came through backport PRs.

### The problem

- Labels as intent don't work. Share of backports whose original had backport label fell from 100% in v1.6.1 to 43% in v1.6.5. Contributors can't set labels at all, PR template asks them for something they can't do, so they open backports by hand.
- Labels are relative, so `kind/backport` started to mean different line when next release line appeared.
- We don't run E2E on every backport PR today: original already passed it on `main`, and we save CI time. But E2E on release line is what actually tests the change there, and running it per backport PR would add a lot of CI time, bad both for shared runners and for cadence. So change gets tested, just on different base.
- Fork backports can't pass CI on release branches (image push needs registry access), need manual run approval and full re-review.
- From merge to `main` till stable release median is 9.8 days, p90 41, and almost all of it is waiting before backport (median 5.9, p90 37.6). 16 fixes over 4 patches were found one patch late, 15 of them had no label.

## Goals

- What goes to patch is decided by written rules, no per-PR human decision.
- One PR per line per batch, tested by usual diff-based CI (TIA).
- Fix PR title and content are trustworthy enough for rules to rely on.
- No backport PRs from contributors, enforced by automation, not by explaining it to each one.
- Maintainer can still include or exclude single PR when rules get it wrong.

### Non-goals

- Changing how rc is cut, tested and promoted (`cut-prerelease.yaml`, `tags.yaml`, `promote-rc.yaml` stay).
- Release cadence of minors.

## Design

We accept the risk of testing batches instead of single backports: one E2E run per batch covers what today goes untested on release line, for fraction of CI time per-PR runs would cost.

### Rules

Batcher takes PR from `main` into release line when all of these hold:

1. it merged to `main` after line branched and line doesn't carry it yet;
2. title type is `fix` or `security`, CI and e2e fixes included;
3. it is not breaking (no `!`, no `BREAKING CHANGE`);
4. it touches no platform migrations, `targetVersion`, `api/` or CRDs, those wait for next minor;
5. it applies to line (clean pick, or conflict resolved) and batch CI passes with it;
6. when two lines are supported, older line takes fix only if newer line already has it, so upgrade from older line to newer never loses a fix.

`feat`, `chore` and deps don't go to patches by rule. If some class of fixes should wait for minor, it becomes new rule, not per-PR call.

### Maintainer overrides

Rules will get some PRs wrong, for example docs fix for old docs or a `fix` that turns out too risky for patch. Two labels on original PR handle that: `backport/include` takes PR even if rules say no (still subject to rule 5), `backport/skip` keeps it out. They go to new `backport/` category in `.github/labels.yml`, same namespaced format as other labels there. Only maintainers can set labels, so this is not a request channel for contributors.

### Check on original PR

Rules trust PR title, so title has to match the code. Checks that happened when someone looked at backport PR move to review of original PR:

- fix comes as its own atomic PR, not mixed with feature or refactoring;
- title type matches the change: `fix` really fixes something, `feat` adds behaviour, breaking change is marked with `!`;
- scope and touched paths agree (for example `fix(docs)` that changes chart templates is wrong).

Check runs on every PR to `main` and reports status with explanation. Wrong title gets fixed or PR gets split before merge. Maintainer review stays the final call, the check makes the mismatch visible.

### Batcher

Batcher has two parts.

`cmd/backport-batch` (Go) is deterministic part:

1. scan: merges to `main` since line branched, what line already carries (`-x` trailers and "Backport of #N" in line history, or pick that changes nothing), override labels;
2. select by rules and overrides;
3. build `backport-batch/<line>-<date>` off line head, one `cherry-pick -x` commit per original in merge order;
4. open or update one PR per line: originals, conflicts, skipped items with reasons, release notes of originals;
5. `backport-batch check <line>` fails while some qualifying fix is neither on line nor in open batch PR, it replaces `backport-audit` as patch-cut gate.

Agent part handles what needs judgement:

- conflict: agent resolves it as separate commit per original that touches only original's files (`backport-batch fold` validates that), PR body shows it with range-diff;
- failed CI: TIA jobs map to packages, so commits that touch failing package get dropped, batch is rebuilt without them and they go to next batch with reason.

Batcher runs on Monday and Thursday. Twice a week is often enough that fix reaches release line in days, and rare enough that nobody spends time on backports every day. Both days leave working days after batch to cut rc, run rc-e2e and promote before weekend.

Maintainer reviews batch PR and merges it, rc is cut from line with the same workflows as today.

### Contributors

PR template says backports are automatic. `pr-labeler.yaml` closes PR opened against `release-*` by author with read access or less, with comment linking the policy. It reacts only on `opened`, so fix that really exists only on release line is reopened by maintainer and stays open.

### What goes away

`cmd/backport-audit`, `backport.yaml` and `kind/backport*` labels. No release workflow depends on them, only docs and contract tests.

## User-facing changes

Fixes reach release lines in about a week, no feature backports into patches. Contributors open atomic `fix` PRs with correct titles and don't open backport PRs.

## Upgrade and rollback compatibility

Nothing changes in clusters, this is process only. Rollback is revert of the change, labels and `backport.yaml` come back with it.

## Security

Batcher works only with commits already merged to `main`. Untrusted input is PR text: titles, bodies and release notes copied into batch PR body are neutralized (mentions, closing keywords, links outside the repo). Agent steps read PR text too, so their output is checked by code: conflict commit must pass `fold` containment, and PR check only reports status. Guard decides from event data and repo permission lookup and never checks out PR code.

## Failure and edge cases

- Conflict agent can't resolve: original goes to next batch, PR body says why.
- Batch CI fails: commits touching failing package are dropped and retried next batch.
- `fix` that is really a feature passes review: maintainer sets `backport/skip`, or reverts it on line.
- Docs fix for old docs: maintainer sets `backport/include`.
- Original depends on PR that is not merged yet: pick conflicts or CI fails, it waits for next batch.
- Contributor opens backport PR: guard closes it, maintainer reopens if it's release-only fix.

## Testing

- Rules: table tests per rule, plus replay on real merges from v1.6.1 to v1.6.5 window as fixtures: every `feat`, breaking and api/CRD change there must be skipped, fixes picked, overrides respected.
- Policy and code stay in sync: rules on policy page are machine-readable block, test fails if code and page differ.
- Batcher: assembly, presence, `--onto` continuation, `fold` containment and `check` exit codes on synthetic git repos.
- Check on original PR: evaluation set of past PRs with known correct type (including mislabeled ones), check has to agree with it before it starts reporting status.
- Guard: bats tests for every condition, including private org members with write access.
- Every batch PR body lists skipped items by rule, so maintainer sees what rules decided on each run.

## Rollout

Everything lands together: docs and policy, guard, batcher, check on original PR, removal of `backport-audit`, `backport.yaml` and backport labels. First batch runs on next Monday or Thursday after merge.

## Open questions

- Agent part of batcher and check on original PR are LLM-based. Agree?

## Alternatives considered

- Keep labels and fix the bot: absolute label per line (like Cilium `needs-backport/X.Y`), App token for CI. Fixes CI and line confusion, but decision stays per PR and contributors still can't label.
- Per-PR triage by human or agent with questions. For v1.6.6 it gave 176 items to look at for one patch, and decision still sits with one person.
- Fully deterministic batcher without agent part. Conflicts would wait for human every time and nothing checks that title matches code, which rules depend on.
