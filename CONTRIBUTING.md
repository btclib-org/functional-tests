# Contributing

What this repository holds in common with the others of the organization
— the toolchain, the lint gate, the tool tables behind it, the workflow
set and the branch rules — is stated once in the
[btclib-org repository standard](https://github.com/btclib-org/.github),
each rule with the alternative it was decided against. It binds this
repository, so a change departing from it is a divergence, and one filed
as an issue in that repository rather than here: a difference between two
repositories belongs to neither of them.

**This file is the same in every repository of the organization up to
its last section.** What is true of one tree only — the commands that
build its environment, the gates it runs, which of its workflows decide
a merge — is under that heading, and the comparison stops there.

How the organization decides, and who holds which role, is
[`GOVERNANCE.md`][governance]; what it intends to do, and what it
deliberately does not, is [`ROADMAP.md`][roadmap]. Both are the
organization's, one copy each beside the standard.

## The issue tracker

Where an issue is filed, and what an alignment finding has to name, is
[the standard's *What this repository is*][s-what]: an issue spanning
repositories, or whose subject is the standard, goes to
[btclib-org/.github](https://github.com/btclib-org/.github/issues), and
one about this tree alone stays here.

A finding noticed while doing something else goes where `REVIEWING.md`'s
*What is filed, and what is not* says, for an author as much as for a
reviewer: a pull request answering two questions cannot be accepted for
either.

An issue labelled `good first issue` is one set aside for a first
contribution: small and self-contained.
[One search][good-first] lists the open ones across the organization, so
a newcomer need not know in advance which repository to look in.

## Documentation and comments

[Section 9 of the standard][s9] is the prose style beyond *Write
plainly* below, and it governs the prose this tree ships — comments,
docstrings and markdown. It is not restated here: a second wording is
the one that goes stale, which is that section's own *One fact in one
place*.

A commit message is prose this tree ships too, though section 9 does not
say so: [the only merge method the rule accepts][s11] puts it on `main`
as the landing commit's body, so what is written in one is read there
long after the branch is gone.

## Working with an LLM

**Development here is LLM-based.** Most issues, code and reviews are
written by agentic sessions that the maintainer directs and reads.
Humans are welcome, as long as they are comfortable working that way.

**A contribution is yours, whoever wrote it.** Read every line before
you open the pull request, understand it, and be ready to answer a
review without the model. Text or code you have not read, or cannot
explain, is closed without review.

**For a pull request the agent opened, whoever approves it reads every
line and answers for it as their own**, the agent being `claude[bot]`,
run on an issue by [section 10][s10]'s `reusable-claude-implement.yml`.
The maintainer, landing it through the bypass without an approval, reads
every line and answers for it instead, the landing being their own act;
requiring an approval even where the bypass is kept is the alternative
set aside. The agent signs off nothing, since a bot cannot certify the
[Developer Certificate of Origin][dco], so the approval, or that
landing, is the certification.

**Write plainly**, in the tree and on the forge: the point first, plain
words, one fact per sentence. On the forge, the title and the first
sentence carry the point; a body is at most two short paragraphs before
any code block or template section; a comment is about 250 words, and
never more than 300, with no list and no heading; a review's summary
takes the shape `REVIEWING.md`'s *The verdict* gives it.

## Pull requests

What `main` accepts, and what it refuses to everyone, is [section 11 of
the standard][s11]. Run the gates locally before opening anything —
the last section of this file says which they are — because CI runs
exactly them, so a red run there is a local run that was not done.

**Every commit of a pull request carries a `Signed-off-by:` trailer
naming its author**, which certifies the [Developer Certificate of
Origin][dco]. `git commit -s` adds it, and `git rebase --signoff <base>`
adds it to commits already made. The `Sign-off` check is required, so
only an administrator can merge a pull request with a commit the check
refuses: *Landing it* says why `--admin` waits for no required check.
The check's failure prints the command that adds the trailer. [The
standard's *Signatures*][s-sigs] says why a signature does not replace
it, and which commits the `Sign-off` job skips.

What a pull request's title and description have to say about the issues
it closes, and why a manual link in the Development panel is a trap
neither of them shows, is [the standard's *What a pull request says it
is*][s-title]. Read it before opening one; it is the rule most often
found broken after the fact.

**Before it is opened, the branch's own commit subjects and bodies are
read against that same rule.** The description does not exist yet to
disagree with them, and [the standard][s-title] has the command that
scans the branch's own commit text for a verb in front of a reference.

**The two spellings are named here as well as there, against [section 9's
*One fact in one place*][s9]**, the paragraph above naming the section
and not the forms, which are the half a citation is got wrong in:
`(closes #N)` cites an issue the change closes, wherever the citation
sits — the title, the commit subject where [*Merge method*][s11] makes
that the thing that lands — and `(issue #N)`
cites, in those same places, an issue the change advances and does *not*
close. One token holds one meaning whichever file it sits in, so the
pair is chosen by what is true of the change rather than by which file
is being written, and a tree's own landed subjects are not what to copy
it from: nothing already landed is rewritten, so what a repository wrote
before the rule stays where it is.

`REVIEWING.md` is the standard a review is written against, and is this
file's other half. Read before opening a pull request, it is what the
pull request will be answered against.

A pull request adds no entry to `CHANGELOG.md` or `RELEASE_NOTES.md`, a
release's own pull request excepted ([section 9][s9], [section 12][s12]).

### One subject, opened as soon as it is written

A pull request answers one question. Issues that share a subject are one
pull request, closing each of them; issues that do not are one pull
request each, however small either of them is.

It is opened the moment it is written and verified — not held for the
previous one to be reviewed or to land, and not batched with the next. A
batch arrives as one reviewing job with several subjects, which is the
shape that costs the most to read; a finished pull request held back is
review that could have started and did not. A security advisory's fix is
the exception, held back as [section 12][s12]'s *A security advisory is
worked in this order* says.

Working this way stacks branches, which is fine and costs one rule: a
child whose base was amended is moved with the old base named,

```shell
git rebase --onto <new-base> <old-base-sha> <child>
```

because a plain rebase replays the base's old commit inside the child,
and the forge then shows the base's old text as additions with nothing
red anywhere. Read the child's diff afterwards rather than trusting the
rebase, and retarget each child onto `main` as its parent lands.

### The review

A review is given promptly and on local evidence. It does not wait for
CI, does not report a check as a finding, and does not discuss a run at
all: whether CI is green is the author's business, once, at landing time.

The exchange is anchored to a sha rather than to a branch, a branch being
free to move under a review:

- the author hands off by naming the sha pushed and the evidence run
  against it, then leaves that head alone;
- the reviewer answers with findings — where, what is wrong, how they
  know it, and whether each is blocking;
- the author accepts what is reasonable, declines the rest with a reason
  in the thread, pushes the answer without waiting for CI, and resolves
  each thread once answered;
- the reviewer reopens a thread whose finding is still blocking, and
  re-reviews the delta rather than the branch.

**What ends the loop is the ack of record**, and the author does not
supply their own. A reading that says what it found and delivers no
verdict is a review too and ends nothing; [the standard's *Review*][s-rev]
has which is which, and `REVIEWING.md` has how each is written. A
disagreement that survives a second exchange goes to the maintainer
instead of into a third round.

**The ack of record is not the approval *Landing it* asks for**: it is
a bot's review of type COMMENT ([the standard's *Review*][s-rev] says
whose).

### Landing it

CI is read once, and this is where: on the head that lands. `main` does
not ask that head to be up to date with it, and [section 11][s-branch]
says why. So a branch is rebased only where it conflicts with `main`. A
rebase that moved nothing but the base leaves the ack standing; one that
resolved a conflict does not, that resolution being a change no reviewer
has seen.

When `main`'s CI is red after a merge, its repair takes priority over
every other pull request, because until it lands `main`'s runs cannot
tell a new failure from the one already there. A pull request armed with
`--auto` (below) stays armed, and others keep landing meanwhile. Holding
every landing for a repair costs more than the break it guards against,
so the risk of landing on a red `main` is accepted
(btclib-org/.github#1777).

A pull request whose required check is red only because `main` was red
is closed and reopened once the repair lands (`gh pr close <n>`, then
`gh pr reopen <n>`). A re-run would test the old merge commit, and the
reopen runs the checks on the merge GitHub then holds. GitHub refreshes
that merge lazily, so the reopen's runs can still test the old one. A
run's log is read before the run is relied on: its line `HEAD is now at
<sha> Merge <head> into <base>` names the merge it tested, and where
`<base>` lacks the repair, the pull request is closed and reopened
again. No push is made, so its approvals and its ack of record stand.
Closing disarms `--auto`, so it is armed again once the checks are
green. The reopen also runs the review bot again, and changes that
review requests are answered like any other review's.

Then squash, [the only method the rule accepts][s11].

**It lands once somebody other than its author has approved that
head**, through `gh pr merge` pinned to it:

```shell
gh pr merge <n> --squash --match-head-commit <the head the checks ran on>
```

With `--auto` added, GitHub runs the same merge once the approval and
the checks are in.

**Where the repository has a bypass, the maintainer may land without
that approval, through it**: [`GOVERNANCE.md`][governance]'s *How a
decision is made* says which repositories.
`--admin` turns off `gh`'s own refusal, and GitHub applies
the bypass to the merge. `enforce_admins` is off, so it waits for no
required check either:

```shell
gh pr merge <n> --squash --admin \
  --match-head-commit <the head the checks ran on>
```

Without `--admin`, `gh pr merge` refuses a head with no approval, saying
it `is not mergeable: the base branch policy prohibits the merge`.

**The pin is not optional**, in either command. Reading the ack and
merging are two calls, and the head is free to move between them — the
push that would move it comes out of the same round the verdict does.
Unpinned, the command takes whatever sits at the head when it runs;
pinned, it refuses a head that has moved, and a round lost that way is
cheaper than a tree nobody has read reaching `main`.
*The review* above anchors the exchange to a sha and [section 11][s11]
has an ack name one: the pin is that rule reaching the call that
performs the landing.

**Verify what landed rather than trusting the answer**, the signature
[the standard asks for][s-sigs] being a valid one rather than a
particular signer's:

```shell
gh api repos/{owner}/{repo}/commits/main \
  --jq '.commit.verification | {verified, reason}'
```

**What it closed is read again here too, from the landed sha rather
than from the pull request**: [the standard's *What a pull request says
it is*][s-title] has the second read, and why the first alone does not
reach a squash subject composed after it runs.

The forge deletes the head branch itself, per the setting section 11
names. What is still yours is bringing every checkout sitting on `main`
up to date,
that being where the next session starts from and a stale one being where
a branch gets built on a base that has moved. `REPOSITORY.md` carries the
settings and why they are what they are.

[s-what]: https://github.com/btclib-org/.github#what-this-repository-is
[s11]: https://github.com/btclib-org/.github#11-github-settings
[s9]: https://github.com/btclib-org/.github#9-prose-comments-and-docstrings
[s10]: https://github.com/btclib-org/.github#10-workflows
[s12]: https://github.com/btclib-org/.github#12-releasing
[s-title]: https://github.com/btclib-org/.github#what-a-pull-request-says-it-is
[s-rev]: https://github.com/btclib-org/.github#review
[s-sigs]: https://github.com/btclib-org/.github#signatures
[s-branch]: https://github.com/btclib-org/.github#branch-protection-and-rulesets
[dco]: https://developercertificate.org/
[governance]: https://github.com/btclib-org/.github/blob/main/GOVERNANCE.md
[roadmap]: https://github.com/btclib-org/.github/blob/main/ROADMAP.md
[good-first]: https://github.com/search?q=org%3Abtclib-org+label%3A%22good+first+issue%22+is%3Aopen&type=issues

## Worktrees

Run `git fetch origin` in the primary checkout, then check that
`git status --porcelain` is empty and `git branch --show-current` is
`main`. Run `git pull --ff-only` only when both checks pass, then verify
`git rev-parse HEAD origin/main` prints the same sha twice. Create a separate
worktree for each task with
`git worktree add <path> origin/main -b <branch>`. After committing,
remove it with `git worktree remove <path>`. If initialized submodules
make that fail,
check for uncommitted, untracked, and ignored files in the worktree and
its initialized submodules. Preserve anything needed before retrying with
`git worktree remove --force <path>`; it can delete those files. Keep the
primary checkout clean and on `main`; do not
switch branches or stash there. Hooks can fix files in place, and the local
`main` and `refs/stash` are shared by all worktrees.

## This repository in particular

Everything above is the same file in every repository of the
organization; everything below is this one's, and the comparison stops at
this heading.

<!-- The toolchain badges are here rather than in the README because they
report no state: each names a choice, and this is the file that says how
the choice is enforced and what the command for it is. The README keeps
the badges that can turn red, the same split some sibling repositories
of the organization use. --> [![calendar versioning:
yyyy.m.d](<https://img.shields.io/badge/cal_ver-yyyy.m.d-1674b1.svg?logo=calver>)](<https://calver.org/>)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)
[![format:
ruff](https://img.shields.io/badge/format-ruff-yellowgreen.svg?logo=ruff)](https://docs.astral.sh/ruff/formatter/)
[![lint:
ruff](https://img.shields.io/badge/lint-ruff-yellowgreen.svg?logo=ruff)](https://docs.astral.sh/ruff/)
[![docstrings:
ruff](https://img.shields.io/badge/docstrings-ruff-yellowgreen.svg?logo=ruff)](https://docs.astral.sh/ruff/rules/#pydocstyle-d)
[![type check:
mypy](https://img.shields.io/badge/type_check-mypy-yellowgreen.svg?logo=mypy)](https://mypy-lang.org/)
[![lint:
markdownlint-cli2](https://img.shields.io/badge/lint-markdownlint--cli2-yellowgreen.svg?logo=markdown)](https://github.com/DavidAnson/markdownlint-cli2)
[![pre-commit
enabled](https://img.shields.io/badge/pre--commit-enabled-brightgreen?logo=pre-commit)](https://github.com/pre-commit/pre-commit)
[![GitHub repository:
btclib-org/functional-tests](https://img.shields.io/badge/GitHub-btclib--org%2Ffunctional--tests-181717?logo=github)](https://github.com/btclib-org/functional-tests/)

To get an overview of the project, read the [README](./README.md) and
[ISS 2220](https://github.com/btclib-org/btclib/issues/2220), the charter
this repository was created under.

What a primitive does -- a curve operation, a signature scheme, a script
-- is btclib's, not this package's: a finding that reproduces with
`btclib` alone is an issue for
[its tracker](https://github.com/btclib-org/btclib/issues) rather than
this one, and a disagreement between two nodes is a finding on the
losing node's own tracker (rule 3 of issue btclib-org/btclib#2220), never
here.

### The one constraint

**This package is built on btclib, its sibling packages and
bitcoin-core-rpc, imports no node, and reaches one only over a socket.**
Rule 1 of
[ISS 2220](https://github.com/btclib-org/btclib/issues/2220). A test is
written in btclib's names, consensus constants keeping Core's spelling
and no alias layer (rule 2).

### The public surface

The adapter under `src/btclib_functional_tests/` -- `bitcoind`, `btcd`,
`btclib_node`, `capability`, `debug_log`, `feature_block`, `mempool_util`,
`mini_wallet`, `node`, `peer`, `socks5`, `timeout_factor` -- is each its own
submodule with its own `__all__`, and
`docs/source/btclib_functional_tests.rst` gives each an `automodule` section
of its own. The package root re-exports none of them and keeps an empty
`__all__` by decision, a caller importing the submodule it needs instead.
**Every module and every package declares `__all__`**, at every depth of
the tree, and `tests/all_test.py` is the census.

### The environment and the gates

uv is the only tool that must be installed; it fetches interpreters,
linters and packaging tools itself. `uv sync` creates the environment.

```shell
uv sync
```

The unit suite reaches no network and starts no node: `tests/integration/`
is the one part of the tree that does, gated on `TF2_INTEGRATION` and
skipping itself without it, matching the switch btclib's own
`tests/integration/` carries -- named `TF2_INTEGRATION` here rather than
`BTCLIB_INTEGRATION`, tf2 being this repository's own label.
`TF2_BITCOIND`, `TF2_BTCLIB_NODE_PYTHON` and `TF2_BTCD` name the nodes:
a `bitcoind` on `PATH` or named directly, the interpreter
`btclib-node` is importable by -- never this project's own, which
carries no group installing it (`pyproject.toml`'s comment on
`[dependency-groups]` has why) -- and a `btcd` on `PATH` or named
directly. Each skips cleanly, naming what to set, rather than failing on
a program this repository does not ship.

```shell
TF2_INTEGRATION=1 uv run pytest tests/integration
```

[tests/README.md](./tests/README.md) is where the suite and its
convention tests are described.

`TF2_BITCOIND` and `TF2_BTCLIB_NODE_PYTHON` name a node built or
installed from a checkout of the developer's own -- a Bitcoin Core
developer's `master`, a btclib-node developer's `main` -- commonly a
clone beside this one (`../bitcoin`, `../btclib-node`) rather than a
copy inside this tree.

A Bitcoin Core developer, against a `bitcoind` built from `master`
(Core's `src/CMakeLists.txt` sets `CMAKE_RUNTIME_OUTPUT_DIRECTORY` to
the build directory's `bin/`):

```shell
cmake -S ../bitcoin -B ../bitcoin/build
cmake --build ../bitcoin/build
TF2_INTEGRATION=1 \
    TF2_BITCOIND=../bitcoin/build/bin/bitcoind \
    uv run pytest tests/integration
```

A btclib-node developer, against their own checkout's own environment,
where `uv sync` installs `btclib-node` editable -- an interpreter
separate from the one running `pytest`, for the reasons above:

```shell
uv sync --project ../btclib-node
TF2_INTEGRATION=1 \
    TF2_BTCLIB_NODE_PYTHON=../btclib-node/.venv/bin/python \
    uv run pytest tests/integration
```

No local run decides a `btclib-node` cell of `TF2.md`, a btclib-node
developer's included: a cell gives what the runs `TF2.md`'s *The
per-test ledger* names measured.

Several nodes named at once run every test that needs one against each,
a disagreement between them being a finding rather than a failure of the
suite (rule 3 of
[ISS 2220](https://github.com/btclib-org/btclib/issues/2220)).

The gate is the suite, the hooks and the documentation build:

```shell
uv run pytest
uv run pre-commit run --all-files
uv run --locked --exact --no-default-groups --group docs \
    sphinx-build -n -W -b html docs/source docs/build/html
```

The documentation gate also runs the checks of the built pages that
*Reproducing what CI runs* gives under `docs.yml`, `check_included_links.py`
among them.

`uv run pytest` above never reaches `tests/integration`'s own bodies:
`TF2_INTEGRATION` is unset, so each skips itself and `[tool.coverage.run]`'s
`omit` leaves the ratchet measuring what that run actually executed.

`--cov` is in `addopts`, so the bare `pytest` above is the coverage gate
and `fail_under` is what it answers against -- 100%, and coverage takes
that literally: a statement or a branch no test reaches fails it. A
selective run is reported and not gated, and `tests/conftest.py`'s
`coverage_fail_under` is what makes that difference.

The documentation build is the one to remember, because no hook reads
reStructuredText: a docstring docutils cannot parse fails it with every
hook green. `-n` turns an unresolved cross-reference into a warning for
`-W` to fail on, and `conf.py`'s `intersphinx_mapping` is what resolves a
reference into the standard library, btclib or bitcoin-core-rpc.

**`--exact` above is not optional.** `uv sync` (this section's first
command) installs `dev`, which carries `pytest`; `--no-default-groups
--group docs` alone only adds what `docs` needs to that same venv and
prunes nothing, so a module importing `pytest` builds locally with no
warning while CI's own job, a fresh venv per run, fails on it
(`capability.py` once imported `pytest`: the local build passed and CI's
`docs` job failed with `ModuleNotFoundError`).

**Check exit codes, not filtered output.** `pre-commit run ... | grep -v
Passed` hides a failure, and `grep` finding nothing exits 1, which is not
the gate's answer to anything.

**The lint gate is not installed as a git hook.** `pre-commit install`
writes into the common git directory, which every worktree of this
repository shares: `git -C <worktree> rev-parse --git-path hooks` answers
with the primary checkout's `.git/hooks` from every one of them. So one
session installing it installs it for every other. Run the gate by hand
before committing -- the `uv run pre-commit run --all-files` above.

One of those hooks needs maintenance, and only one. `.secrets.baseline`
carries findings of the `Secret Keyword` plugin, none of them a secret --
`btclib_node.py`'s placeholder `_RPC_PASSWORD` among them; the addresses
`rpc_validateaddress_test.py` copies from Core raise none, and this tree
vendors no golden file. Its two entropy plugins are off from the first
scan, the same way every other repository of the organization keeps
them off: a 40-character commit SHA is what most of `.pre-commit-config.yaml`'s
own `rev:` pins are, and a hex string that long is indistinguishable
from a high-entropy secret to a detector that does not know what a git
pin looks like. Adding a vector or a golden file later, or seeing a new
false positive from a legitimate high-entropy string, means
regenerating:

```shell
uvx --from detect-secrets detect-secrets scan \
    --disable-plugin Base64HighEntropyString \
    --disable-plugin HexHighEntropyString \
    --baseline .secrets.baseline
```

Read the resulting diff before committing it. That is the whole point of
a baseline rather than an exclusion: what appears in it is what nobody
has looked at yet, so regenerating without reading turns the review into
a formality.

### Running against a Core developer's own build

Issue [functional-tests#36](https://github.com/btclib-org/functional-tests/issues/36):
a Core developer runs this suite beside `test/functional`, or in place
of it, with the options `test_framework.py` and `test_runner.py` give
them.

**The interpreter.** `requires-python` is `>=3.12`, by the maintainer's
own decision departing from section 1 of the organization's standard
and from btclib-org/.github#1324 -- `pyproject.toml`'s own comment on
`requires-python` has the measurement, `typing.override` and `sphinx`
both needing 3.12 and nothing here needing more. `uv run` fetches that
interpreter on its own where none is on `PATH`, whatever floor is
named: measured under `UV_PYTHON_PREFERENCE=only-managed` and a fresh
`UV_PYTHON_INSTALL_DIR`, with no matching interpreter visible anywhere
first, `uv run` downloaded the interpreter `.python-version` names and
ran on it. So a Core developer needs no interpreter of their own either
way; lowering the floor is what lets one who already has 3.12 or newer
skip that download.

**A `bitcoind` run by hand** passes `-nosettings`, and
`-datadir=<a scratch directory>` for anything that can start a node.
Without `-nosettings`, even `-help` reads and rewrites the default
datadir's `settings.json`, as `bitcoind.py`'s `_has_wallet` says.
Without `-datadir`, a node runs in the default datadir. On a developer's
machine, that datadir can hold real wallets.

**Options.** Core's own option is the row's key, every one either file
takes at Core's `master` or at `v32.1`; `--` is a cell this suite has
nothing under.

| Core's option | this suite's equivalent |
| --- | --- |
| `--nocleanup` | pytest's own `--basetemp=<dir>`; see below |
| `--cachedir` | -- (no pregenerated-datadir cache exists) |
| `--tmpdir` | pytest's own `--basetemp=<dir>` |
| `-l`, `--loglevel` | -- (no central logger to set a level on) |
| `--tracerpc` | `--tracerpc`, `tests/integration/conftest.py` |
| `--portseed` | -- (`node.free_ports`: one block per `pytest-xdist` worker) |
| `--previous-releases` | -- (no previous-release binaries mechanism) |
| `--coveragedir` | -- (no RPC-coverage instrumentation) |
| `--configfile` | -- (`bitcoind.py`'s `_has_wallet` probes the binary) |
| `--pdbonfailure` | pytest's own `--pdb` |
| `--usecli` | -- (rule 1 reaches a node only over RPC) |
| `--valgrind` | through the adapter; see below |
| `--randomseed` | `pytest-randomly`'s own `--randomly-seed` |
| `--timeout-factor` | `--timeout-factor`, `tests/integration/conftest.py` |
| `--v2transport` | through the adapter; see below |
| `--v1transport` | through the adapter; see below |
| `--test_methods` | pytest's own node ids, or `-k` |
| `-f`, `--fff` | -- (Core's dummy argument for IPython) |
| `--ansi` | pytest's own `--color=yes\|no\|auto` |
| `--combinedlogslen`, `-c` | -- (no combined log; `--basetemp` names each) |
| `--coverage` | -- (paired with `--coveragedir` above) |
| `--exclude`, `-x` | pytest's own `--deselect`, `--ignore`, or `-k 'not ...'` |
| `--extended` | -- (every family runs; no extended/basic split) |
| `--help`, `-h`, `-?` | pytest's own `-h` |
| `--jobs`, `-j` | `pytest-xdist`'s own `-n` |
| `--quiet`, `-q` | pytest's own `-q` |
| `--tmpdirprefix`, `-t` | pytest's own `--basetemp=<dir>` |
| `--failfast`, `-F` | pytest's own `-x` |
| `--filter` | pytest's own `-k` |
| `--resultsfile`, `-r` | pytest's own `--junitxml=<path>` |

`--nocleanup`: nothing in this tree's own fixtures ever removes a
node's datadir -- `NodeAdapter.stop` (`node.py`) only terminates the
process -- and pytest's own `tmp_path`/`tmp_path_factory` do not delete
a run's directories either when that run ends, only trimming older
numbered ones (`tmp_path_retention_count`, default 3) the *next* time a
run starts. `tests/integration/nocleanup_bitcoind_test.py` measures the
first half directly; naming `--basetemp=<dir>` is what makes the
directory a Core developer can find rather than one of the numbered
`pytest-of-<user>` ones.

`--tracerpc`: a `--tracerpc` flag on `tests/integration`'s own
collection wraps every adapter's RPC transport
(`bitcoin_core_rpc.BitcoinCoreRpcClient`'s own `transport=`) and prints
each request and reply as it is made, matching Core's own wording. An
adapter reads the flag only through its own `trace_rpc`, so every one a
test builds goes through the `make_adapter` fixture beside the option,
and `tests/tracerpc_reach_test.py` fails on a construction that goes
around it without naming a `trace_rpc` of its own.

`--timeout-factor`: scales every wait this suite's own adapters and
`Peer` make by default -- `NodeAdapter.start`'s own startup wait,
`NodeAdapter.stop`'s own wait for the process to exit,
`NodeAdapter.wait_until_stopped`, `connect_nodes`,
`wait_until`, `wait_until_disconnected`, `wait_until_tips_agree`,
`wait_until_mempools_agree` and `assert_debug_log` in `node.py` and
`debug_log.py` (`disconnect_nodes` and `sync_all` through the waits they
call), and `Peer`'s own
connection and per-call timeouts and `Listener.accept`'s own wait in
`peer.py` -- through
`timeout_factor.py`'s own `scaled`, set once per process by
`tests/integration/conftest.py`'s own `pytest_configure`, the same
per-process scope `SkipCounts` already carries for the same `-n auto`
reason. `node.wait_until` is the shared, scaled loop
`tests/integration/`'s own test modules poll a predicate through, rather
than each reimplementing one unscaled beside it
([ISS 90](https://github.com/btclib-org/functional-tests/issues/90)).
Each RPC client every adapter builds bounds a call by `timeout_factor.py`'s
own `rpc_client_timeout`: Core's own `rpc_timeout`, scaled, then halved.
The same `pytest_configure` scales `pyproject.toml`'s own per-test
`timeout` by the factor, unless pytest-timeout's own `--timeout` or
`PYTEST_TIMEOUT` names a bound of the caller's own
([ISS 91](https://github.com/btclib-org/functional-tests/issues/91)).
A test with a Core wait at or past that `timeout` marks itself
`scaled_timeout(seconds)` and gets a `timeout` of `seconds`, scaled the same
way; a bound of the caller's own still applies instead
([ISS 309](https://github.com/btclib-org/functional-tests/issues/309)).
`--timeout-factor 0` scales by 999, which is how Core's own
`BitcoinTestFramework.parse_args` carries out its help's "Setting it to 0
disables all timeouts": every wait above is still a bound, only a long one,
and so is a wait a test expects to expire.

`--v2transport` and `--v1transport`: through the adapter rather than a
pytest option, since this suite has no central test-framework object
for a flag like Core's own to set a default on. An adapter's own
`extra_args`, `("-v2transport=1",)` or `("-v2transport=0",)`, is Core's
own flag, node by node; `tests/integration/v2transport_option_test.py`
passes it to `NodeAdapter.restart` and measures both directions against
`getpeerinfo`'s own `transport_protocol_type`.
`Capability.V2TRANSPORT` covers node-to-node connections and a `Peer`'s
own: `Peer` (`peer.py`) speaks the v1 wire unless it is asked for v2.
`BitcoindAdapter` always declares it and `BtclibNodeAdapter` declares it
per build; `btclib_node.py`'s module docstring says which builds.

`--valgrind`: no pytest option, since valgrind wraps a process rather
than a test; a `TF2_BITCOIND` naming a wrapper script that execs the
real binary under valgrind reaches the same effect through the adapter,
`NodeAdapter` never inspecting the executable path it is given beyond
passing it to `subprocess.Popen`.

### A version, and no release

Nothing here is released: `pyproject.toml`'s `version = "0"` is a
placeholder, no tag has ever agreed with it, and no command derives it.
Section 12's calendar versioning applies to a release. This repository
ships by being read.

### The editor

`.vscode/settings.json` and `.vscode/extensions.json` are tracked, and they
hold no preference: the recommended extensions are the tools
`.pre-commit-config.yaml` already runs, and the settings put the fixing ones
on save. Installing them is optional and changes nothing about what a local
run enforces.

Anything machine-local -- an interpreter path, a telemetry answer, a theme --
belongs in the editor's own user settings instead, those two files being
read by every checkout of this repository.

### Reproducing what CI runs

Each command below is the one a CI job runs. Keep this section true when a
workflow changes.

`test.yml`, the `coverage` job:

```shell
uv run --locked --no-default-groups --group test pytest
```

`lint.yml`, the `lint` job -- this file *is* the lint gate, so there is no
second list of tools anywhere:

```shell
uv run --locked --only-group lint \
    pre-commit run --all-files --show-diff-on-failure
```

`docs.yml`, the `docs` job -- the build, and then reads of the pages it
wrote. `reusable-docs.yml`'s own command carries no `--exact`, needing
none: a job's venv is fresh per run, which is what `--exact` reproduces
locally, added below for that reason and not present in the workflow
itself:

```shell
uv run --locked --exact --no-default-groups --group docs \
    sphinx-build -n -W -b html docs/source docs/build/html
if grep -rn 'href="#\.\.\?/' docs/build/html --include='*.html'; then
    echo "::error::the links above resolve to no page (unresolved relative path)"
    exit 1
fi
```

What myst renders for a destination it cannot resolve is an anchor to an
id no page has. `-W` reports it because `docs/source/conf.py` resolves the
links the included root files carry and suppresses no myst warning; the
`grep` is what still finds one the day a suppression goes back in.

Last, the job runs btclib-org/.github's `check_included_links.py`, at the
commit `docs.yml` pins, over the same pages. It fails on a link to a file
the docs render that is left at the repository's `/blob/main/` address
rather than at the file's page here. Section 2 of the standard, *The
documentation*, is the rule:

```shell
served=$(mktemp -d)
git clone --quiet --filter=blob:none --sparse \
    https://github.com/btclib-org/.github "$served"
git -C "$served" sparse-checkout set .github/scripts
git -C "$served" checkout --quiet \
    "$(sed -n 's/.*reusable-docs\.yml@\([0-9a-f]*\).*/\1/p' .github/workflows/docs.yml)"
found=0
uv run --no-project --python 3.15 \
    "$served"/.github/scripts/check_included_links.py docs/build/html || found=$?
rm -rf "$served"
test "$found" -eq 0
```

`vendored-vectors.yml`, the `vectors` job re-checks every framework pin
of `TF2.md` already at upstream's tip; the `census` job re-reads
`test/functional/test_framework/` recursively and compares it to the
ledger in both directions, then compares each per-test pin with its
path's tip:

```shell
python .github/scripts/check_vendored_vectors.py \
    TF2.md "TF2.md pins behind upstream" --dry-run
python .github/scripts/tf2_ledger_census.py \
    TF2.md "test_framework has files TF2.md has no entry for" \
    "TF2.md per-test rows behind upstream" --dry-run
```

`codeql.yml` has no line here: this repository does not carry it yet.

`sourcery.yml` has no line here: section 10 of
[the organization's standard](https://github.com/btclib-org/.github#the-set-and-its-cadence)
says why it is a pull request's check and not a local gate.

`node-integration.yml`, the `bitcoind` job -- btclib-org/.github's
`reusable-integration-bitcoind.yml` is what runs it, so there is no
second command beyond the one above: `TF2_INTEGRATION=1 uv run pytest
tests/integration` against a bitcoind that job installs and verifies.
The `btclib-node` job needs the second interpreter the workflow's own
header explains, so reproducing it is that same command with
`TF2_BTCLIB_NODE_PYTHON` pointed at an interpreter `btclib-node` was
installed into -- one its own `requires-python` admits and its
`rocksdict` dependency ships a wheel for, `rocksdict` publishing no
sdist -- the job's own being the one its `Setup a second interpreter
for btclib-node` step names. `btclib-node-main`
is the same command again, `TF2_BTCLIB_NODE_PYTHON` pointed at an
interpreter carrying that project's own `main` rather than its last
release. Each of the two compares its JUnit report with `TF2.md`'s
`btclib-node` column, `release` or `main` naming whose verdict a cell
gives:

```shell
python .github/scripts/btclib_node_verdict.py \
    TF2.md integration.xml release
```

`btcd` is `TF2_BTCD` pointed at the btcd release the job installs,
with only the btcd tests run, and `.github/scripts/check_node_ran.py`
over its JUnit report; the release's sha256 is in the job's `env`:

```shell
TF2_INTEGRATION=1 TF2_BTCD=<btcd v0.26.2> \
    uv run --locked --no-default-groups --group test \
    pytest tests/integration/*_btcd_test.py --junitxml=integration.xml
uv run --locked --no-default-groups --group test \
    python .github/scripts/check_node_ran.py integration.xml
```

`older-core` is `TF2_BITCOIND` pointed at the release under test, v29.4
or v30.3, with only the bitcoind tests run; each release's sha256 is in
the job's matrix:

```shell
TF2_INTEGRATION=1 TF2_BITCOIND=<v29.4 or v30.3 bitcoind> \
    uv run pytest tests/integration/*_bitcoind_test.py
```

`core-master` is `TF2_BITCOIND` pointed at a `bitcoind` built locally
from Bitcoin Core's own `master`, the job's own `Configure the build`
step naming the CMake options; its own verdict on a failure unique to
that build is `.github/scripts/tf2_master_verdict.py`, given the two
runs' own JUnit reports.

### What gates a merge, and what only reports

`lint.yml`, `test.yml`, `docs.yml`, `sourcery.yml` and
`node-integration.yml`'s `bitcoind` job produce the required checks, and
`REPOSITORY.md` reads the rule back from the endpoint rather than
restating it. So a diff does not reach a review without having passed
them or passing them beside it on the same sha, which is the reliance
`REVIEWING.md` provides for.

`node-integration.yml`'s other jobs -- `older-core`, `btclib-node`,
`btcd`, `core-master` and `btclib-node-main` -- gate nothing anywhere:
`continue-on-error: true` in the workflow itself says so for each.
`older-core` runs v29.4 and v30.3, which do not move; it is informational
by decision, so that a flake on an older release blocks no merge.
`btcd` runs a pinned release too; README.md's opening says when a test it
fails is a finding.
`btclib-node` installs btclib-node's latest release unpinned, so a new
release moving a cell of `TF2.md`'s `btclib-node` column turns it red for
a reason outside any pull request; `core-master` and `btclib-node-main`
track Core's own `master` and btclib-node's own `main` rather than the pinned
release under test, by [ISS 8](https://github.com/btclib-org/functional-tests/issues/8)'s
decision of 2026-09-25.

`verified-signatures.yml` reports after each push to `main` whether every
commit pushed is `verified`; [the standard's *Signatures*][s-sigs] has the rule.

| workflow | when | what it varies |
| --- | --- | --- |
| `test` | pull request, push | -- |
| `lint`, `docs` | pull request, push | -- |
| `node-integration` | pull request, push | -- |
| `claude-review` | pull request, and `@claude` in a comment | -- |
| `sourcery` | pull request | -- |
| `links` | weekly | -- |
| `verified-signatures` | push to `main` | -- |
| `vendored-vectors` | weekly | the pins in `TF2.md`, and the census |

Which day each of the rest runs is section 10 of
[the organization's standard](https://github.com/btclib-org/.github),
and not this file's to restate.

The required checks run on `ubuntu-latest`. The suite, pre-commit, the
docs build and the integration tests run on the version
`.python-version` names; Sourcery's analysis runs on the interpreter its
wheel bundles, which the header of btclib-org/.github's
`reusable-sourcery.yml` names. `claude-review` gates nothing: a review
that gates a merge would make a model's judgement a branch rule.
