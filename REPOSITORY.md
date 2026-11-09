# Repository configuration

Read this before changing a workflow, a branch rule or a repository
setting; writing code does not need it. `AGENTS.md` points here rather than
carrying it, so that a session fixing a bug in the package does not hold it
in context.

The branch rules and the repository settings live *outside* the
repository. What is recorded is the settings the organization's
standard asks about — the ones section 16's checklist sets on a new
repository, the ones a section of the standard states a rule for, and
the ones a behaviour it describes rests on — together with whatever a
call quoted for one of those answers alongside it. That is this file's
scope, and *What this file passes over* at the foot says what falls
outside it.

The topics and `.homepage` have a second form in the tree —
`pyproject.toml`'s `keywords` and its `[project.urls] homepage` — so
each is read back here for comparison rather than as the only place the
answer lives, which is what *Topics* and *Creating the repository* say
of them. This tree publishes no package: *Publishing* below says what
is absent. *Read the Docs* below records the project the docs build on.

Each section carries the command that sets its setting and the command
that reads it back, and the `#` lines under a read-back are what it
printed on 2026-09-25 — section 16's last step — unless a later date
stands beside it. Section 11 makes such an answer documentation, with a
reader as its check: nothing re-runs these commands, so an answer that
differs today is a change made since that date.

## Creating the repository

GitHub redirects the repository's former names, `bitcoin-node-tests`
and `btclib-functional-tests`, to this one, and a read-back below dated
before 2026-10-31 ran under the first. Read back on 2026-11-02:

```shell
gh api repos/btclib-org/bitcoin-node-tests --jq .full_name
# btclib-org/functional-tests
gh api repos/btclib-org/btclib-functional-tests --jq .full_name
# btclib-org/functional-tests
gh api repos/btclib-org/functional-tests --jq '{name, description}'
# {"description":"Bitcoin Core's functional tests rewritten on btclib,
#  with bitcoind as the oracle","name":"functional-tests"}
```

The name and the calls that follow stand in blocks of their own. The
repository is public, and that is a prerequisite rather than a
preference: branch protection is a paid feature for a private repository
on the free plan.

```shell
gh repo create btclib-org/functional-tests --public \
  --description "Bitcoin Core's functional tests rewritten on btclib, \
with bitcoind as the oracle" \
  --disable-wiki
```

`main` is pushed next, being the branch every rule below names, and the
read-back below is what says it is the default:

```shell
git push git@github.com:btclib-org/functional-tests.git main
```

Then the switches section 11 turns off or on at the repository level, in
one call — the projects board and the wiki off, the tracker on, squash as
the only merge method with auto-merge, the head branch deleted on merge,
and the squash commit's title and body — together with the `.homepage`,
section 3's page on btclib.org:

```shell
gh api -X PATCH repos/btclib-org/functional-tests \
  -F has_issues=true -F has_wiki=false -F has_projects=false \
  -F allow_squash_merge=true -F allow_merge_commit=false \
  -F allow_rebase_merge=false -F allow_auto_merge=true \
  -F delete_branch_on_merge=true \
  -f squash_merge_commit_title=COMMIT_OR_PR_TITLE \
  -f squash_merge_commit_message=COMMIT_MESSAGES \
  -f homepage=https://btclib.org/functional-tests/
```

Read back:

```shell
gh api repos/btclib-org/functional-tests \
  --jq '{visibility, default_branch: .default_branch, has_issues,
         wiki: .has_wiki, projects: .has_projects}'
# {"default_branch":"main","has_issues":true,"projects":false,
#  "visibility":"public","wiki":false}
gh api repos/btclib-org/functional-tests/pages
# {"message":"Not Found", ...}, gh: Not Found (HTTP 404)
diff <(gh api repos/btclib-org/functional-tests --jq .homepage) \
     <(sed -n '/^\[project.urls\]/,/^\[/p' pyproject.toml \
       | sed -n 's/^homepage = "\(.*\)"$/\1/p')
# (empty)
```

`has_issues` is what `CONTRIBUTING.md`'s *The issue tracker* rests on,
and so does `.github/ISSUE_TEMPLATE/`. The Pages call is expected to
answer `404`: this tree serves no GitHub Pages site, and a recorded `404`
is what makes a later flip visible. The `diff` compares `.homepage` with
`pyproject.toml`'s `[project.urls] homepage`, and is expected empty; it
was read on 2026-11-05.

## Required checks on main

**Never name matrix contexts in the branch rule.** The rule lives outside
the repository, so a context that stops being produced blocks every merge
with nothing in the tree to explain why. `test: every job passed` is an
aggregate job at the end of `test.yml` that `needs` every other job there;
a new job in `test.yml` belongs in that job's `needs`, or it gates
nothing.

`main` requires these checks and nothing else:

| Check | Produced by |
| --- | --- |
| `test: every job passed` | `test.yml`, aggregate over its jobs |
| `docs / Build the documentation` | `docs.yml`, calling `reusable-docs.yml` |
| `lint / Lint and type-check` | `lint.yml`, calling `reusable-lint.yml` |
| `bitcoind / Regtest against Bitcoin Core` | `node-integration.yml` |
| `lint / Dependency review` | `lint.yml`, calling `reusable-lint.yml` |
| `lint / Sign-off` | `lint.yml`, calling `reusable-lint.yml` |
| `sourcery / Sourcery` | `sourcery.yml`, calling `reusable-sourcery.yml` |

A merge through the maintainer's bypass waits for none of these checks,
as `CONTRIBUTING.md`'s *Landing it* says.

A job whose whole body is a call to a reusable workflow contributes no
name of its own: the context joins the calling job's id to the called
job's own name, so `docs.yml`'s `docs` job calling a job named `Build the
documentation` produces `docs / Build the documentation`, and
`node-integration.yml`'s `bitcoind` job calling a job named `Regtest
against Bitcoin Core` produces `bitcoind / Regtest against Bitcoin Core`.
A context is matched by name, not by the workflow that reported it, so
moving a job is free and renaming one is not.

[Section 10 of the
standard](https://github.com/btclib-org/.github#the-set-and-its-cadence)
says when a tree requires `sourcery / Sourcery`.

`node-integration.yml`'s other jobs -- `older-core`, `btclib-node`,
`core-master` and `btclib-node-main` -- produce no required check: each
runs with `continue-on-error: true` and reports rather than gates.
`older-core` runs v29.4 and v30.3, which do not move; it is informational
by decision, so that a flake on an older release blocks no merge. `btclib-node`
installs btclib-node's latest release unpinned, so a new release moving
a cell of `TF2.md`'s `btclib-node` column would fail a pull request for a
reason outside it; `core-master` and
`btclib-node-main` track Core's own `master` and btclib-node's own
`main`, and a required check depending on either would fail a pull
request for a reason outside it ([ISS
8](https://github.com/btclib-org/functional-tests/issues/8), decided
2026-09-25).

No sentinel appears in the rule, and none of them may: `links.yml` and
`vendored-vectors.yml` can each go red for reasons no pull request
introduced, and a red check nobody can act on from a branch is noise.

Every required check is an Actions check, bound to the app that produces
it — `checks` with an `app_id` rather than the bare `contexts` list, 15368
for Actions — so nothing else can satisfy one.

## Branch protection

Classic protection carries the required checks with `strict` off (section 11
has the ground), one approving review, `dismiss_stale_reviews`, linear
history, no force pushes, no deletions, `required_conversation_resolution`,
and `enforce_admins` **off**. The whole object is `PUT` once, here, on a
branch that has none; every later change `PATCH`es the sub-endpoint it is
about, a partial `PUT` dropping the rest:

```shell
gh api -X PUT \
  repos/btclib-org/functional-tests/branches/main/protection \
  --input - <<'JSON'
{"required_status_checks": {"strict": false, "checks": [
   {"context": "test: every job passed", "app_id": 15368},
   {"context": "docs / Build the documentation", "app_id": 15368},
   {"context": "lint / Lint and type-check", "app_id": 15368}]},
 "enforce_admins": false,
 "required_pull_request_reviews": {"dismiss_stale_reviews": true,
   "require_code_owner_reviews": false,
   "required_approving_review_count": 1},
 "restrictions": null,
 "required_linear_history": true,
 "allow_force_pushes": false,
 "allow_deletions": false,
 "required_conversation_resolution": true}
JSON
```

The body arrives on stdin because `-f` sends every value as a string,
where the endpoint types `app_id` as an integer.

Read back:

```shell
gh api repos/btclib-org/functional-tests/branches/main/protection \
  --jq '{checks: [.required_status_checks.checks[] | [.context, .app_id]],
         strict: .required_status_checks.strict,
         reviews: .required_pull_request_reviews
                  | {required_approving_review_count, dismiss_stale_reviews},
         enforce_admins: .enforce_admins.enabled,
         linear: .required_linear_history.enabled,
         conversation: .required_conversation_resolution.enabled}'
# (Read on 2026-11-05T07:17:35Z, after sourcery / Sourcery was added.)
# {"checks":[["test: every job passed",15368],
#            ["docs / Build the documentation",15368],
#            ["lint / Lint and type-check",15368],
#            ["bitcoind / Regtest against Bitcoin Core",15368],
#            ["lint / Dependency review",15368],
#            ["lint / Sign-off",15368],
#            ["sourcery / Sourcery",15368]],
#  "conversation":true,"enforce_admins":false,"linear":true,
#  "reviews":{"dismiss_stale_reviews":true,
#             "required_approving_review_count":1},"strict":false}
```

`bitcoind / Regtest against Bitcoin Core`, `lint / Dependency review`,
`lint / Sign-off` and `sourcery / Sourcery` were added after the `PUT`
above. A check added later goes through the sub-endpoint alone, the
sentence above's reason for never repeating the whole `PUT`:

```shell
branch=repos/btclib-org/functional-tests/branches/main
gh api -X PATCH "$branch"/protection/required_status_checks \
  --input - <<'JSON'
{"strict": false, "checks": [
   {"context": "test: every job passed", "app_id": 15368},
   {"context": "docs / Build the documentation", "app_id": 15368},
   {"context": "lint / Lint and type-check", "app_id": 15368},
   {"context": "bitcoind / Regtest against Bitcoin Core", "app_id": 15368},
   {"context": "lint / Dependency review", "app_id": 15368},
   {"context": "lint / Sign-off", "app_id": 15368},
   {"context": "sourcery / Sourcery", "app_id": 15368}]}
JSON
```

Read back:

```shell
gh api "$branch"/protection/required_status_checks \
  --jq '{checks: [.checks[] | [.context, .app_id]], strict}'
# (Read on 2026-11-05T07:17:35Z, after sourcery / Sourcery was added.)
# {"checks":[["test: every job passed",15368],
#            ["docs / Build the documentation",15368],
#            ["lint / Lint and type-check",15368],
#            ["bitcoind / Regtest against Bitcoin Core",15368],
#            ["lint / Dependency review",15368],
#            ["lint / Sign-off",15368],
#            ["sourcery / Sourcery",15368]],
#  "strict":false}
```

Three rulesets sit beside it, additive — rules aggregate across rulesets
and classic protection, taking the most restrictive combination.
`main-integrity` has no bypass actor, for anyone:

```shell
gh api -X POST repos/btclib-org/functional-tests/rulesets --input - <<'JSON'
{"name": "main-integrity", "target": "branch", "enforcement": "active",
 "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
 "bypass_actors": [],
 "rules": [{"type": "required_linear_history"},
           {"type": "non_fast_forward"},
           {"type": "deletion"}]}
JSON
```

`main-self-merge` requires a pull request, with the maintainer as the
only bypass actor and in **`pull_request` mode**, which excuses the
approving review and nothing else: a direct push to `main` is refused for
everyone. btclib-org/.github's
[`GOVERNANCE.md`](https://github.com/btclib-org/.github/blob/main/GOVERNANCE.md#how-a-decision-is-made),
*How a decision is made*, says when the maintainer uses it, and the
commands that land a pull request are `CONTRIBUTING.md`'s *Landing it*.

```shell
gh api -X POST repos/btclib-org/functional-tests/rulesets --input - <<'JSON'
{"name": "main-self-merge", "target": "branch", "enforcement": "active",
 "conditions": {"ref_name": {"include": ["refs/heads/main"], "exclude": []}},
 "bypass_actors": [{"actor_id": 3296421, "actor_type": "User",
                    "bypass_mode": "pull_request"}],
 "rules": [{"type": "pull_request", "parameters": {
   "allowed_merge_methods": ["squash"],
   "dismiss_stale_reviews_on_push": true,
   "require_code_owner_review": false,
   "require_last_push_approval": false,
   "required_approving_review_count": 1,
   "required_review_thread_resolution": true}}]}
JSON
```

`3296421` is `fametrano`'s account id,
`gh api users/fametrano --jq .id`.

Classic protection's own copy of the signature rule is off, as it is in
every ruleset over `main`: [the standard states that value for every
repository](https://github.com/btclib-org/.github/blob/main/README.md#branch-protection-and-rulesets).
Read on 2026-10-15:

```shell
gh api repos/btclib-org/functional-tests/branches/main/protection \
  --jq '.required_signatures.enabled'
# false
```

So nothing refuses an unsigned commit at the push.
`verified-signatures.yml` checks after the push: for each commit pushed it
reads `.commit.verification.verified` from the API and fails naming any
whose value is not `true`. A red run is read in the Actions tab; the check
is not a required one. What a pull request writes to `main` is the squash
GitHub composes, which carries its web-flow key, and [the standard asks for
a verified commit and not for a particular
signer](https://github.com/btclib-org/.github/blob/main/README.md#signatures).

## Tag protection

`tag-integrity`, `target: tag`, `refs/tags/v*`: required signatures, and
nothing else, matching every other repository of the organization even
though no release is tagged here:

```shell
gh api -X POST repos/btclib-org/functional-tests/rulesets --input - <<'JSON'
{"name": "tag-integrity", "target": "tag", "enforcement": "active",
 "conditions": {"ref_name": {"include": ["refs/tags/v*"], "exclude": []}},
 "bypass_actors": [],
 "rules": [{"type": "required_signatures"}]}
JSON
```

`GET /rulesets` answers a summary per ruleset, with neither `rules` nor
`bypass_actors` among its keys, so those two come from the single-ruleset
endpoint:

```shell
for id in $(gh api repos/btclib-org/functional-tests/rulesets \
    --jq '.[].id'); do
  gh api repos/btclib-org/functional-tests/rulesets/"$id" \
    --jq '{name, target, enforcement, include: .conditions.ref_name.include,
           rules: [.rules[].type],
           bypass: [.bypass_actors[] | [.actor_id, .bypass_mode]],
           methods: [.rules[] | select(.type=="pull_request")
                              | .parameters.allowed_merge_methods]}'
done
# {"bypass":[],"enforcement":"active","include":["refs/heads/main"],
#  "methods":[],"name":"main-integrity",
#  "rules":["required_linear_history","non_fast_forward","deletion"],
#  "target":"branch"}
# {"bypass":[[3296421,"pull_request"]],"enforcement":"active",
#  "include":["refs/heads/main"],"methods":[["squash"]],
#  "name":"main-self-merge","rules":["pull_request"],"target":"branch"}
# {"bypass":[],"enforcement":"active","include":["refs/tags/v*"],
#  "methods":[],"name":"tag-integrity","rules":["required_signatures"],
#  "target":"tag"}
```

A `v*` tag push that brings an unsigned commit is refused. A tag on a
commit already on `main` is accepted whether it is signed, unsigned or
lightweight, so the rule does not check a tag's signature
(btclib-org/.github#1635).

## Merge methods

**Squash is the only method GitHub can be asked for**, and auto-merge
presses it once the review and the checks are in; the call is the one
under *Creating the repository*. Read back:

```shell
gh api repos/btclib-org/functional-tests \
  --jq '{allow_squash_merge, allow_merge_commit, allow_rebase_merge,
         allow_auto_merge, squash_merge_commit_title,
         squash_merge_commit_message, delete_branch_on_merge}'
# {"allow_auto_merge":true,"allow_merge_commit":false,
#  "allow_rebase_merge":false,"allow_squash_merge":true,
#  "delete_branch_on_merge":true,
#  "squash_merge_commit_message":"COMMIT_MESSAGES",
#  "squash_merge_commit_title":"COMMIT_OR_PR_TITLE"}
```

`COMMIT_OR_PR_TITLE` is the subject: the pull request title with its
number, or the subject of the single commit where a branch has one.
`COMMIT_MESSAGES` is the body, and it is where a `Co-Authored-By`
trailer written in the branch's commits survives the squash.
`delete_branch_on_merge` keeps the branch list a list of live work; a
pull request closed without merging keeps its head branch.

## Token permissions

**The default `GITHUB_TOKEN` is read-only repository-wide**, so a job
needing more must declare it, and the declarations are the record of
which jobs do:

```shell
git grep -nE '^ +[a-z-]+: write([[:blank:]]+#|$)' -- .github/workflows
```

Each grant that command finds carries its reason on its own line. The
workflow-level `permissions: contents: read` every workflow declares is
belt and braces, and it is what makes the intent readable in the file.

Whether this repository pins its own default or inherits the
organization's is unreadable afterwards — no endpoint reports an
override — so it is left to inherit, and this is the call that reads the
value it gets:

```shell
gh api repos/btclib-org/functional-tests/actions/permissions/workflow \
  --jq '{default_workflow_permissions, can_approve_pull_request_reviews}'
# {"can_approve_pull_request_reviews":false,
#  "default_workflow_permissions":"read"}
```

The expected answer is `read` and `false`. Where it is not, the
organization default has moved, and section 11's command is the one that
moves it back for the organization rather than here.

## Allowed actions and SHA pinning

Read on 2026-09-27:

```shell
gh api repos/btclib-org/functional-tests/actions/permissions
# {"enabled":true,"allowed_actions":"all","sha_pinning_required":true}
gh api orgs/btclib-org/actions/permissions \
  --jq '{allowed_actions, sha_pinning_required}'
# {"allowed_actions":"all","sha_pinning_required":true}
```

The organization gives the same two answers, `sha_pinning_required`
being set at that level: [section 11 has the reasons for both
fields](https://github.com/btclib-org/.github/blob/main/README.md#tokens-publishing-scanning).

## Sign-off on web commits

```shell
gh api repos/btclib-org/functional-tests --jq .web_commit_signoff_required
# true
gh api orgs/btclib-org --jq .web_commit_signoff_required
# true
```

Set at the organization level, [for the reason section 11
gives](https://github.com/btclib-org/.github/blob/main/README.md#signatures).

## Publishing

**Nothing is published.** No `release.yml`, no `pypi-install.yml`:
section 2 of the organization's standard does not ask them of a
tier-2 tree, which this is.

**No `pypi` or `testpypi` environment exists on this repository.**
Section 11's own rule is that each is named by a job of the release
workflow and carries a review from that job's own gate; an environment
nothing names and nothing gates is found at the endpoint and not
recorded here, and the tree that names neither job is this one, so
neither is recorded:

```shell
gh api repos/btclib-org/functional-tests/environments --jq .total_count
# 0
```

Each would arrive with `release.yml`, in the pull request that adds the
job naming it.

## Read the Docs

**Imported, and it builds.** The project is on
[readthedocs.org](https://app.readthedocs.org/) under the slug
`btclib-functional-tests`. The repository has no webhook; whether the
organization-wide `read-the-docs-community` GitHub App connects the
project is answered by no public endpoint, and was not checked. Read
back on 2026-11-02:

```shell
p=https://app.readthedocs.org/api/v3/projects/btclib-functional-tests
curl -s "$p/" | jq -c '{default_branch, default_version, repository: .repository.url}'
# {"default_branch":"main","default_version":"latest",
#  "repository":"https://github.com/btclib-org/functional-tests.git"}
curl -s "$p/versions/?active=true" \
  | jq -c '.results[] | [.slug, .type, .identifier]'
# ["latest","branch","main"]
curl -s "$p/builds/35547358/?expand=config" \
  | jq -c '{commit, success, python: .config.build.tools.python}'
# {"commit":"d6def10b5f97133d460a2ceb4bb04e0dc9e4ea7f","success":true,
#  "python":{"full_version":"3.14.6","version":"latest"}}
gh api repos/btclib-org/functional-tests/hooks --jq length
# 0
```

The former slug, `bitcoin-node-tests`, redirects its pages to this
project's. Read back on 2026-10-31:

```shell
curl -sI https://bitcoin-node-tests.readthedocs.io/en/latest/index.html \
  | grep -i -E '^(HTTP|location|x-rtd-redirect)'
# HTTP/2 301
# location: https://btclib-functional-tests.readthedocs.io/en/latest/index.html
# x-rtd-redirect: user
```

`stable` is absent from the active versions for the reason
`btclib-wallet`'s section gives: no `v*` tag exists, this tree
having published nothing (*Publishing*, above). Whether an automation
rule is set for a tag it may gain is Admin-only and
answered by no public endpoint; it was not checked here.

**`full_version` is not the interpreter a build runs the project's own
code under.** Read the Docs' `build.tools.python: "latest"` resolves to
CPython 3.14.6 (`config.build.tools.python.full_version` of the build
above); `.readthedocs.yaml`'s comment on that key says why it decides
nothing. The same build's log, read back on 2026-10-31, names the
interpreter that `.python-version` does:

```shell
curl -s https://app.readthedocs.org/api/v2/build/35547358/ \
  | jq -r '.commands[].output' | grep 'Using CPython'
# Using CPython 3.12.3 interpreter at: /usr/bin/python3.12
```

Nothing here waits on Read the Docs reaching a newer `"latest"`.

## Security settings

Secret scanning, its push protection, Dependabot alerts, Dependabot
security updates and private vulnerability reporting are free on a
public repository and off by default:

```shell
gh api -X PATCH repos/btclib-org/functional-tests --input - <<'JSON'
{"security_and_analysis": {
   "secret_scanning": {"status": "enabled"},
   "secret_scanning_push_protection": {"status": "enabled"}}}
JSON
gh api -X PUT repos/btclib-org/functional-tests/vulnerability-alerts
gh api -X PUT repos/btclib-org/functional-tests/automated-security-fixes
gh api -X PUT \
  repos/btclib-org/functional-tests/private-vulnerability-reporting
```

Code scanning's **default setup** has to stay off, this tree carrying no
`codeql.yml` yet either way, and the code-quality setting beside it:

```shell
gh api -X PATCH -F state=not-configured \
  repos/btclib-org/functional-tests/code-scanning/default-setup
gh api -X PATCH -F state=not-configured \
  repos/btclib-org/functional-tests/code-quality/setup
```

Read back:

```shell
gh api repos/btclib-org/functional-tests --jq '.security_and_analysis'
# {"dependabot_security_updates":{"status":"enabled"},
#  "secret_scanning":{"status":"enabled"},
#  "secret_scanning_non_provider_patterns":{"status":"disabled"},
#  "secret_scanning_push_protection":{"status":"enabled"},
#  "secret_scanning_validity_checks":{"status":"disabled"}}
gh api -i repos/btclib-org/functional-tests/vulnerability-alerts | head -1
# HTTP/2.0 204 No Content
gh api repos/btclib-org/functional-tests/automated-security-fixes
# {"enabled":true,"paused":false}
gh api repos/btclib-org/functional-tests/private-vulnerability-reporting
# {"enabled":true}
gh api repos/btclib-org/functional-tests/code-scanning/default-setup \
  --jq .state
# not-configured
gh api repos/btclib-org/functional-tests/code-quality/setup --jq .state
# not-configured
```

The alerts endpoint has no body and answers with its status, 204 for
enabled and 404 for not. Private vulnerability reporting is what
`.github/ISSUE_TEMPLATE/config.yml` sends a reporter to (no
`SECURITY.md`: section 2 gives that file to a tier-1 tree); with it
disabled that link is a 404.

## Topics

Section 3 makes a package's `keywords` its topics, entry for entry, so
the topics are set from `pyproject.toml` rather than typed a second time:

```shell
sed -n '/^keywords = \[/,/^]/s/^ *"\(.*\)",$/\1/p' pyproject.toml \
  | jq -R . | jq -s '{names: .}' \
  | gh api -X PUT repos/btclib-org/functional-tests/topics --input -
```

Read back, and compared, sorted because GitHub returns topics in an order
of its own; the diff is expected empty:

```shell
diff <(gh api repos/btclib-org/functional-tests --jq '.topics[]' \
       | sort) \
     <(sed -n '/^keywords = \[/,/^]/s/^ *"\(.*\)",$/\1/p' pyproject.toml \
       | sort)
# (empty)
```

The keywords, in order: `bitcoin`, `bitcoin-core`, `conformance-testing`,
`functional-tests`, `p2p`, `rpc`.

## Plan-gated settings

**The ceiling on concurrent jobs lives here**, and nowhere else in this
tree: it is a number the plan decides rather than anything this repository
configures, so prose that needs the reasoning — a workflow header,
`CONTRIBUTING.md` — states the ceiling unnumbered and points here:

```shell
gh api orgs/btclib-org --jq .plan.name
# free
```

[GitHub's own table](https://docs.github.com/en/actions/reference/limits)
is what turns that answer into a number, shared across every repository
in the organization. That is what every trade above spends: a check
required of a pull request holds a slot every other pull request in the
organization then waits for.

Secret scanning's non-provider patterns and validity checks need paid
Secret Protection, and the API answers a `PATCH` with 200 while leaving
them disabled. The `detect-secrets` hook is the compensating control.

## What this file passes over

The endpoints above answer for more than this repository decides, and the
scope at the top is what leaves the rest out.

**Most of the repository document is not a setting.**
`gh api repos/btclib-org/functional-tests --jq 'keys[]'` answers with
URLs, counts, timestamps and derived state beside the switches, and the
switches among them this file records are the ones a section above reads
back with a call of its own.

**A switch no section of the standard states a rule for stays out.**
`allow_forking`, `allow_update_branch`, `has_discussions`,
`has_downloads` and `is_template` are in that document and no section
above reads any of them back.

**A credential this repository spends and does not hold.**
`claude-review.yml` reads `secrets.CLAUDE_CODE_OAUTH_TOKEN`, which
section 11 makes an organization secret at `visibility=all` in both
stores, so the repository's own stores are expected empty and a copy in
either would be that decision undone:

```shell
gh api repos/btclib-org/functional-tests/actions/secrets --jq .total_count
# 0
gh api repos/btclib-org/functional-tests/dependabot/secrets --jq .total_count
# 0
```

**A switch this repository does not set.** `claude-review.yml` calls
`btclib-org/.github`'s `reusable-claude-review.yml`, whose jobs guard on
`vars.CLAUDE_REVIEW_ENABLED`; a variable set here would take precedence
over one of the same name on the organization, so the repository's own
store is read too:

```shell
gh api repos/btclib-org/functional-tests/actions/variables --jq .total_count
# 0
```
