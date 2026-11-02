# Contributor Onboarding

Everything needed to get a first change merged into Cozystack, in the order you need it. No prior knowledge of the project is assumed.

## 1. Pick what you want to work on

| I want to… | Start here |
|---|---|
| Take a first, small task | [`good first issue`](https://github.com/cozystack/cozystack/issues?q=is%3Aopen+is%3Aissue+label%3A%22good+first+issue%22) |
| Fix a bug someone reported | [`kind/bug` issues](https://github.com/cozystack/cozystack/issues?q=is%3Aopen+is%3Aissue+label%3Akind%2Fbug) |
| Improve documentation | [cozystack/website](https://github.com/cozystack/website) |
| Add an application to the platform | [external-apps-example](https://github.com/cozystack/external-apps-example) |
| Propose an architectural change | [Design proposals](../design-proposals/README.md) |
| Talk to someone first | [Telegram](https://t.me/cozystack), [CNCF Slack #cozystack](https://cloud-native.slack.com/archives/C08BQJD95J7), or the [community meeting](../community_meeting.md) |

Every `good first issue` should name a mentor — the person to ask when you get stuck. If one looks like a good starting point but names nobody, say so in a comment and we will assign someone.

## 2. Work out what environment you need

Cozystack is a platform distribution, so setup cost depends entirely on what you touch. Check this before you start, not after:

| Your change | What you need | How to verify |
|---|---|---|
| Helm charts, `values.yaml`, `values.schema.json`, ApplicationDefinitions, dashboards, docs, CI scripts | **Nothing but Go and Docker** | `make unit-tests` at the repo root; `make show` in the package directory |
| Go controllers, the aggregated API server, CRDs | A local Kubernetes cluster | `make test-controllers`, then deploy the one component you changed |
| Talos, KubeVirt, storage, networking, anything end-to-end | **Three QEMU VMs — 8 vCPU and 24 GiB each**, KVM, root, IPv4 forwarding | `make prepare-env && make test` |

Most first contributions are in the first row and need no cluster at all. If your change lands in the third row and you do not have that hardware, say so on the issue — we can give you time on a shared development cluster rather than have you hunt for 72 GiB of RAM.

For how the platform is actually put together — the operator, the reconciliation chain, the package layout — read the [Developer Guide](https://cozystack.io/docs/v1.6/development/).

## 3. Set up your fork

```bash
git clone https://github.com/<you>/cozystack.git
cd cozystack
git remote add upstream https://github.com/cozystack/cozystack.git
```

Work on a branch off `upstream/main`, never on `main` itself.

## 4. Regenerate what is generated

Several files per package are produced from `values.yaml` and `values.schema.json` and must stay in step with them:

- `packages/(apps|extra)/<name>/README.md` — the parameter table
- `packages/(apps|extra)/<name>/values.schema.json` — ordering and derived fields
- `packages/system/<name>-rd/cozyrds/<name>.yaml` — the ApplicationDefinition

Before committing edits to any of those sources:

```bash
make -C packages/<apps-or-extra>/<name> generate
git add packages/<apps-or-extra>/<name>/ packages/system/<name>-rd/
```

CI runs `make generate` across every package and then `git diff --exit-code`, so un-staged generator output fails the build and blocks the PR. Re-run it after a `git commit --amend` too, if the amended change touched those sources.

To find which packages a branch needs regenerating:

```bash
git diff --name-only | xargs -n1 dirname | sort -u | grep ^packages/
```

## 5. Verify before you open anything

| You changed | Run |
|---|---|
| Helm templates | `make show` in the package directory — it must render cleanly |
| Go code | `make generate && make manifests` at the repo root, then commit the regenerated files |
| `values.yaml` annotations | `make generate` in the package, then review the `values.schema.json` diff |
| Anything at all | `make unit-tests` at the repo root |

## 6. Commit

[Conventional Commits](https://www.conventionalcommits.org/), always with `--signoff`:

```bash
git commit --signoff -m "fix(postgres): update operator to version 1.2.3"
```

**Types:** `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`.

**Scopes** — pick the most specific one that describes the change; the list is not exhaustive and a genuinely new area may introduce its own:

- System: `dashboard`, `platform`, `operator`, `cilium`, `kube-ovn`, `linstor`, `fluxcd`, `cluster-api`
- Apps: `postgres`, `mariadb`, `redis`, `kafka`, `clickhouse`, `virtual-machine`, `kubernetes`
- Other: `api`, `hack`, `tests`, `ci`, `docs`, `maintenance`

Breaking changes: append `!` after the type or scope (`feat(api)!: …`), or add a `BREAKING CHANGE:` footer.

**The sign-off is not optional.** The DCO check fails without a `Signed-off-by:` line matching the commit author, and nobody else can add it for you — it is your statement, not the project's. If you forgot:

```bash
git commit --amend --signoff
git push --force-with-lease
```

Also link the email address you commit with to your GitHub account, or the commit will not be attributed to your profile and your work will not appear in the contributor statistics.

If an AI agent materially helped write the change, add the `Assisted-by: LLM` trailer alongside the sign-off. The trailer discloses assistance without naming a model or vendor: do not put a model or vendor name into a trailer or an authorship line, including a `Co-authored-by:` trailer or a `Generated with <tool>` footer, and never add a `Claude-Session:` trailer or a link to an assistant session to a commit message, PR description, or comment.

```bash
git commit --signoff --trailer "Assisted-by: LLM" -m "fix(postgres): update operator to version 1.2.3"
```

## 7. Rebase before opening the PR

```bash
git fetch upstream
git checkout -b my-feature upstream/main
git cherry-pick <your-commit-hash>
git push -f origin my-feature
```

## 8. Open the pull request

**The title follows the same Conventional Commits format as the commit** — it is parsed by CI to apply `kind/*` and `area/*` labels automatically. A non-conventional title, or a scope nobody has mapped yet, lands the PR in `area/uncategorized`, which means a human has to sort it out by hand.

Fill in [the PR template](https://github.com/cozystack/cozystack/blob/main/.github/PULL_REQUEST_TEMPLATE.md): a `## What this PR does` section, the `release-note` block, and the downstream checklist. If you create the PR from the command line, start from the template rather than passing a plain body — `--body` and `--body-file` replace the body wholesale and silently drop its checklists:

```bash
cp .github/PULL_REQUEST_TEMPLATE.md /tmp/pr-body.md
# fill it in, then:
gh pr create --draft --title "type(scope): brief description" --body-file /tmp/pr-body.md
```

Opening it as a draft is welcome. So is asking for opinions in the community chats while it is still a draft.

### If your change reaches another repository

Cozystack is upstream for repositories that are **not** kept in sync with it automatically, and nothing in CI compares the two sides — so a change here can break them silently. The most common case: adding, renaming or removing a package under `packages/apps/` or `packages/extra/` needs a follow-up in [cozystack/website](https://github.com/cozystack/website), whose docs generator works from a hardcoded app list and simply skips anything missing from it.

The full trigger map — which change forces what, and which file to touch — is in [`docs/agents/contributing.md`](https://github.com/cozystack/cozystack/blob/main/docs/agents/contributing.md#downstream-repositories). Walk it against your actual diff, not against your PR title. Tick a repository only after opening the follow-up there and linking it; a ticked box with no link claims work that does not exist. If the follow-up is out of scope, open an issue in that repository instead and link that.

Unsure? Leave the boxes empty and say so in the PR body, so a human decides.

## 9. What happens next

- Automated reviewers (CodeRabbit, Gemini, CodeQL) comment within minutes. They
are advisory. **A bot comment is not a review** — wait for a human.
- A human response is due within two business days. If it has been longer, ping
on the PR or in [Telegram](https://t.me/cozystack). That is a failure on our side, not impatience on yours.
- Reviewers for your area come from
[`.github/CODEOWNERS`](https://github.com/cozystack/cozystack/blob/main/.github/CODEOWNERS) and are requested automatically.
- For a bug fix, expect to be asked for a regression test that proves the bug
cannot recur — not just an assertion on one field.
- Only **unresolved** review threads are actionable; resolved ones are already
handled. The GitHub UI marks them, and there is a `gh api graphql` recipe for listing them in [`docs/agents/contributing.md`](https://github.com/cozystack/cozystack/blob/main/docs/agents/contributing.md#fetching-unresolved-review-comments).

### Some red CI is ours, not yours

Pull requests opened from a fork cannot authenticate to the project image registry, so build jobs that push images fail regardless of what your change does. If the only failures you see are `Build …` jobs reporting `denied: Anonymous users are only allowed read access`, that is our infrastructure and we are fixing it — do not try to work around it, and do not assume your PR has been rejected. Ask if you are unsure which failures are yours.

## 10. Growing from here

Contributing regularly can lead to owning an area as a Reviewer, and from there to Maintainer. The roles, their requirements and the nomination process are in [CONTRIBUTOR_LADDER.md](https://github.com/cozystack/cozystack/blob/main/CONTRIBUTOR_LADDER.md). Nobody has to nominate themselves — if you are contributing consistently, maintainers are expected to notice and propose you. If you want the role and think you are close, asking is entirely legitimate.
