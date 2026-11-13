# btclib-functional-tests

Bitcoin Core's functional test suite, rewritten on
[btclib](https://github.com/btclib-org/btclib), as a non-regression
suite for any node that speaks bitcoin's RPC and p2p -- its own
repository rather than a feature of one node's own test suite, so that
it can sit beside, or replace, Core's own `test/functional/test_framework/`
(issue btclib-org/btclib#2220). tf2 is this project's nickname and its
label.

The ambition is a suite for every full node speaking that protocol:
today [Bitcoin Core](https://github.com/bitcoin/bitcoin),
[btclib-node](https://github.com/btclib-org/btclib-node) and
[btcd](https://github.com/btcsuite/btcd), tomorrow whichever other
implementation gains an adapter -- a `NodeAdapter` subclass declaring
what it can do (`src/btclib_functional_tests/node.py`), the way `bitcoind.py`,
`btclib_node.py` and `btcd.py` already do. A test passes on `bitcoind`
before its failure on any other node counts as a finding, filed on that
node's own tracker. A `btclib-node` failure where the test is the defect,
not the node, is filed in this repository instead (`TF2.md`'s per-test
ledger).

It imports [btclib](https://github.com/btclib-org/btclib) and
[bitcoin-core-rpc](https://github.com/btclib-org/bitcoin-core-rpc). It
imports no node: what it needs from one, it gets over a socket -- a
process, an RPC connection, a p2p connection.

`TF2.md` is the ledger of what Core's `test/functional/test_framework/`
needs and what covers it, one entry per file, each pinned to the
revision it was read at.

## Running it against your own tree

The suite is meant to be run by the people who change a node: a Bitcoin
Core developer against their own `master`, a btclib-node developer
against their own `main`, each a clone beside this one -- for instance
all three under one `upstream/` folder.

This repository publishes nothing:
`git clone` and `uv sync` is how a checkout gets an environment, uv
being the only tool that has to be installed.

The unit suite reaches no network and starts no node.
`tests/integration/` does, against a node a checkout of your own
provides -- a `bitcoind` built from a Bitcoin Core checkout, an
interpreter `btclib-node` is installed into, or a `btcd` -- and skips
cleanly without one. [CONTRIBUTING.md](./CONTRIBUTING.md)'s *The
environment and the gates* has the commands.

A test's skip names a capability that the node it ran against, or the
wallet factory it was given, does not declare, and the run prints one
count per capability rather than passing in silence.

## CI

`node-integration.yml`'s `bitcoind` job is a required check: it installs
the pinned Bitcoin Core release under test and fails the run if the
tests it starts do not run. Its `btclib-node` job runs the same suite
from PyPI and reports without gating, a disagreement there being a
finding on that node's own tracker rather than a defect of this
repository's gates. The pinned release stays the only required oracle:
`core-master` builds `bitcoind` from Bitcoin Core's own `master`, and
`btclib-node-main` installs from that project's own `main`, both by
[ISS 8](https://github.com/btclib-org/functional-tests/issues/8)'s
decision, and both report without gating. `btcd` runs the btcd tests against
a pinned btcd release, and reports without gating too.

## Contributing

Development is LLM-based: humans are welcome, as long as they are
comfortable with agentic development ;-). A contribution must have been
read and understood by the person who submits it.
[`CONTRIBUTING.md`](./CONTRIBUTING.md#working-with-an-llm) has the rules.

[CONTRIBUTING.md](./CONTRIBUTING.md) also has the commands each CI job runs,
verbatim. [REVIEWING.md](./REVIEWING.md) is what a pull request is
answered against. How the organization decides, and who holds which
role, is its
[GOVERNANCE.md](https://github.com/btclib-org/.github/blob/main/GOVERNANCE.md);
what it intends to do, and what it deliberately does not, is its
[ROADMAP.md](https://github.com/btclib-org/.github/blob/main/ROADMAP.md).

## Links

- Source: <https://github.com/btclib-org/functional-tests>
- [CHANGELOG.md](./CHANGELOG.md)

## Status

<!-- Section 2 of the organization's standard says which badges sit here. -->
[![pre-commit.ci status](https://results.pre-commit.ci/badge/github/btclib-org/functional-tests/main.svg)](https://results.pre-commit.ci/latest/github/btclib-org/functional-tests/main)
[![lint workflow status](https://github.com/btclib-org/functional-tests/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/btclib-org/functional-tests/actions/workflows/lint.yml?query=branch%3Amain)
[![test workflow status](https://github.com/btclib-org/functional-tests/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/btclib-org/functional-tests/actions/workflows/test.yml?query=branch%3Amain)
[![docs workflow status](https://github.com/btclib-org/functional-tests/actions/workflows/docs.yml/badge.svg?branch=main)](https://github.com/btclib-org/functional-tests/actions/workflows/docs.yml?query=branch%3Amain)
[![vendored-vectors workflow status](https://github.com/btclib-org/functional-tests/actions/workflows/vendored-vectors.yml/badge.svg?branch=main)](https://github.com/btclib-org/functional-tests/actions/workflows/vendored-vectors.yml?query=branch%3Amain)
[![links workflow status](https://github.com/btclib-org/functional-tests/actions/workflows/links.yml/badge.svg?branch=main)](https://github.com/btclib-org/functional-tests/actions/workflows/links.yml?query=branch%3Amain)
