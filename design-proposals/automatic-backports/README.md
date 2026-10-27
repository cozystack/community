# Automatic backports

- **Title:** `Automatic backports to release lines`
- **Author(s):** `@myasnikovdaniil`

## Overview

Backports stop being a per-PR decision. Everything that can go to patch release goes there by rule: twice a week a batcher picks fixes merged to `main` onto each supported release line, newest line first, as one PR per line. Nobody requests backports, labels for them or opens backport PRs by hand. Since rules read PR title, title and content of original PR are checked at review of that PR, not at backport time.

## Decisions

- [`0001`: Each original reaches the release line as a merge commit with its own commits](decisions/0001-each-original-reaches-the-line-as-a-merge-with-its-own-commits.md)

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
4. it touches no platform migrations or `targetVersion`, changes no CRD document, and has no API change that API review gate calls sizeable (description-only API changes pass), those wait for next minor;
5. its commits apply to line (clean picks, or conflict resolved) and batch CI passes with it;
6. when two lines are supported, older line takes fix only if newer line already has it merged (open batch PR doesn't count), so older line is never ahead of newer one and upgrade from older line to newer never loses a fix.

`feat`, `chore` and deps don't go to patches by rule. If some class of fixes should wait for minor, it becomes new rule, not per-PR call.

Line released before this policy keeps patch content its users chose it with: it takes only fixes merged after the day policy starts, older ones need `backport/include`. Today that is release-1.6. Every newer line starts at its fork point.

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

1. scan: merges to `main` since line branched, what line already carries (trailer or `-x` line naming original's merge commit, `-x` lines of PR's commits or "Backport of #N" in line history, or picks that change nothing), override labels;
2. select by rules and overrides;
3. build `backport-batch/<line>-<date>` off line head, one merge commit per original in merge order;
4. open or update one PR per line: originals, conflicts, skipped items with reasons, release notes of originals;
5. `backport-batch check <line>` fails while some qualifying fix is neither on line nor in open batch PR, it replaces `backport-audit` as patch-cut gate.

Each original lands in batch the way it sits on `main`, as merge commit with PR's own commits inside. For original merged to `main` as M, batcher picks every commit of the PR in order (`git rev-list --reverse --no-merges M^1..M^2`) on top of batch head with `cherry-pick -x`, then adds merge commit: first parent is batch head before the picks, second parent is last pick. Merge commit carries message of M and a trailer naming M. Commits that merged `main` into PR branch are skipped, they bring changes of other PRs. Original merged as single-parent commit (squash) is one plain pick. Batch PR is merged into line with merge commit as today, so on line each PR looks the same as on `main`.

Picks keep author and `Signed-off-by` of every commit. Reverting one original after batch merged is one command, `git revert -m 1` of its merge commit on line.

Agent part handles what needs judgement:

- conflict: agent resolves original that batcher couldn't pick (below), PR body shows resolved picks with range-diff against PR's commits;
- failed CI: agent drops originals that touch failing package and rebuilds batch, dropped ones go to next batch with reason.

If any commit of the PR doesn't apply, batcher aborts whole original and none of its commits stay in batch. Agent resolves it on top of batch, or it goes to next batch. This shape costs more conflicts than one squashed pick: intermediate commit can fail to apply even when PR's final diff would apply.

`backport-batch fold` is the check resolver runs on resolved original before pushing it to batch branch. Resolution passes when it is one merge commit with current batch head as first parent, commits it brings through second parent are only picks of PR's commits, each with `-x` line of the PR commit it came from, and together they change only files the PR changes. For squashed original it is one pick with `-x` of M that changes only M's files.

Batch PR has CI check that runs the same rule for every original in batch, clean picks included. `fold` runs only where conflict was resolved, so agent misled by PR text could skip it, and without this check nothing would look at the result again after push.

Batcher runs on Monday and Thursday. Twice a week is often enough that fix reaches release line in days, and rare enough that nobody spends time on backports every day. Both days leave working days after batch to cut rc, run rc-e2e and promote before weekend.

Maintainer reviews batch PR and merges it, rc is cut from line with the same workflows as today.

### LLM parts and fallback

Conflict resolution is LLM-based already today: backport conflicts are resolved by LLM agents under maintainer supervision, commit trailers show it. Correctness of a merge depends on agent the same way as correctness of original code, human guides the model and builds test harness around it. So agent resolves conflicts by default, and maintainer reviews batch PR as any other PR.

Title check on PR to `main` is LLM-based too: author writes title, check reports mismatch, then human or human's agent reviews PR. After merge title is trusted, batcher doesn't check it again.

Model access is Copilot provided to the project by CNCF, the same way as CI runners.

### Manual mode

Each LLM step is defined as a task with fixed inputs, allowed output and a check in code, not as a call to a particular model. Instructions for each task live in the repo, so CI agent, maintainer's own agent (Claude Code, Codex and so on) or maintainer by hand get the same task and their result passes the same check:

| Task | Input | Output | Check |
|---|---|---|---|
| Resolve conflict | conflict list in batch PR body, PR's commits | one merge commit per original on batch branch | `backport-batch fold`, same rule in batch PR CI check |
| Failed batch CI | failing jobs of batch PR | original deferred to next batch | `backport-batch run --defer <N>` rebuilds batch without it |
| Title check | PR diff, title | mismatch report on PR | review |

Manual mode is the same flow with a different executor. It's used when model is unavailable, when CI agent result is worse than what maintainer's agent does, or when agent gives up on a task. Scheduled run still builds batch and opens PR, conflicts and failures stay listed in PR body and maintainers get alert. Maintainer checks out batch branch, gives task to own agent or does it by hand, runs the check and pushes. Switching between CI agent and manual mode is a repo setting, nothing else in the flow changes. Nothing critical happens while a task waits: red batch doesn't merge, red rc-e2e blocks promote.

### Contributors

PR template says backports are automatic. `pr-labeler.yaml` closes PR opened against `release-*` by author with read access or less, with comment linking the policy. It reacts only on `opened`, so fix that really exists only on release line is reopened by maintainer and stays open.

### What goes away

`cmd/backport-audit`, `backport.yaml` and `kind/backport*` labels. No release workflow depends on them, only docs and contract tests.

## User-facing changes

Fixes reach release lines in about a week, no feature backports into patches. Contributors open atomic `fix` PRs with correct titles and don't open backport PRs.

## Upgrade and rollback compatibility

Nothing changes in clusters, this is process only. Rollback is revert of the change, labels and `backport.yaml` come back with it.

## Security

Batcher works only with commits already merged to `main`, the same code we review and ship, so its agent part reads nothing that didn't pass review. Text copied into batch PR body (titles, bodies, release notes) is neutralized: mentions, closing keywords, links outside the repo. Conflict commit must pass `fold` containment. Title check on PR is advisory and not the only check, review stays. Guard decides from event data and repo permission lookup and never checks out PR code.

## Failure and edge cases

- Conflict agent can't resolve: original goes to next batch, PR body says why, maintainer can resolve it by hand.
- Batch CI fails: agent drops originals touching failing package and retries them next batch. If it can't find the culprit, maintainer splits batch.
- rc-e2e fails after batch merged: promote is blocked as today, maintainer reverts culprit original on line (`git revert -m 1` of its merge commit) and cuts new rc.
- Previous batch PR is not merged by next run: batcher says so in the PR and alerts maintainers, alerting is set up together with scheduled run.
- Change reached line without `-x` line (manual pick, squash): re-pick is either empty and counted as present, or conflicts and resolver sees change is already there. After rollout batch is the only way to release lines and always adds `-x` lines and trailer naming original.
- `fix` that is really a feature passes review: maintainer sets `backport/skip`, or reverts it on line.
- Docs fix for old docs: maintainer sets `backport/include`.
- Original depends on PR that is not merged yet: pick conflicts or CI fails, it waits for next batch.
- Contributor opens backport PR: guard closes it, maintainer reopens if it's release-only fix.

## Testing

- Rules: table tests per rule, plus replay on real merges from v1.6.1 to v1.6.5 window as fixtures: every `feat`, breaking and api/CRD change there must be skipped, fixes picked, overrides respected.
- Policy and code stay in sync: rules on policy page are machine-readable block, test fails if code and page differ.
- Batcher on synthetic git repos: assembly (PR with several commits, PR that merged `main` in, squashed original), presence, `--onto` continuation, `check` exit codes. `fold` and batch PR check have to refuse commit from outside the PR, pick without `-x` and change outside PR's files.
- Check on original PR: evaluation set of past PRs with known correct type (including mislabeled ones), check has to agree with it before it starts reporting status.
- Guard: bats tests for every condition, including private org members with write access.
- Every batch PR body lists skipped items by rule, so maintainer sees what rules decided on each run.

## Rollout

Everything lands together: docs and policy, guard, batcher with batch PR check, check on original PR, removal of `backport-audit`, `backport.yaml` and backport labels. First batch runs on next Monday or Thursday after merge.

## Open questions

- Title check on PR to `main` is LLM-based and advisory, review stays the final call. Agree?
- Conflict resolution in batch is done by LLM agent by default, with maintainer fallback and batch PR review. Agree?

## Alternatives considered

- Keep labels and fix the bot: absolute label per line (like Cilium `needs-backport/X.Y`), App token for CI. Fixes CI and line confusion, but decision stays per PR and contributors still can't label.
- Per-PR triage by human or agent with questions. For v1.6.6 it gave 176 items to look at for one patch, and decision still sits with one person.
- Fully deterministic batcher without agent part. Conflicts would wait for human every time and nothing checks that title matches code, which rules depend on.
