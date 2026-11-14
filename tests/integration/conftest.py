# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""What the integration tests need, and what makes them skip.

Two switches per node, and `TF2_INTEGRATION` gates both: these tests
start a real process, open a real socket and write to disk, none of
which an ordinary `uv run pytest` should do to somebody who asked for
the unit suite. The second switch is the node itself -- found on `PATH`
or named for bitcoind and btcd, importable by the named interpreter for
btclib-node -- because an opt-in that fails for a missing program would
report a defect that is not one.

Read from btclib's own `tests/integration/conftest.py` for shape, not
shared with it: this repository's own `NodeAdapter` subclasses are what
start and stop a node here (rule 5 of issue btclib-org/btclib#2220 --
tf2 writes its own adapter), where btclib's fixture spawns `bitcoind` by
hand, having no adapter of its own to reach for.

    TF2_INTEGRATION=1 uv run pytest tests/integration

`--tracerpc` and `--timeout-factor` are Core's own `test_framework.py`
options, restated as pytest ones -- CONTRIBUTING.md's own *Running
against a Core developer's own build* has the full mapping, and
`--nocleanup`, `--v2transport` and `--v1transport` are named there too,
neither needing a flag of its own here.

A Core test converged onto several nodes is one body
([ISS 125](https://github.com/btclib-org/functional-tests/issues/125)):
a function in `<file>_test.py`, named for Core's own file
(`p2p_block_sync_test.py`), taking the adapters it runs on and
`skip_counts`. Its name carries no `test_` prefix, so pytest collects no
test from that module. Each `<file>_<node>_test.py` calls it from a test
of the same name with the prefix, handing it that node's own fixture:
`<node>_adapter` for one node, `<node>_cluster` for several, where
`<node>` is `bitcoind`, `btclib_node` or `btcd`. A wallet test's
`<file>_btclib_wallet_test.py` hands its body `btclib_wallets` beside
`bitcoind_cluster`. The body asks
`require` of the instances it was handed, never of an adapter class:
`BtclibNodeAdapter.__init__` widens an instance's `capabilities` by
probing the build it runs. A body that writes into its node's data
directory, or starts it on a chain other than regtest or with an option
from its first start, takes `make_adapter`, the adapter class, the
node's executable fixture (`bitcoind_path`, `btclib_node_python` or
`btcd_path`) and `tmp_path` instead, and `skip_counts` where it asks for
a capability: it builds the node itself over a data directory in
`tmp_path`, asks `require` of that instance, and stops the node before
it returns (`feature_includeconf_test.py`). The test modules stay one
per node, rather than one parametrized over the nodes, because the CI
jobs select a node's tests by module: `node-integration.yml`'s
`btclib-node` jobs run `*_btclib_node_test.py`, its `core-master` job
`*_bitcoind_test.py`, its `btcd` job `*_btcd_test.py`, and its
`btclib-wallet` jobs `*_btclib_wallet_test.py`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.btcd import BtcdAdapter
from btclib_functional_tests.btclib_node import BtclibNodeAdapter
from btclib_functional_tests.btclib_wallet_adapter import BtclibWalletFactory
from btclib_functional_tests.capability import SkipCounts
from btclib_functional_tests.node import NodeAdapter, free_ports
from btclib_functional_tests.timeout_factor import (
    factor_from_option,
    scaled,
    set_factor,
)
from tests.conftest import (
    AdapterFactory,
    fold_worker_tally,
    stash_or_report,
    stop_all,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterator
    from typing import Any

# one tally per process, shared by every fixture and test below:
# `pytest_sessionfinish` reports it once, rather than once per node this
# session happened to drive. Under `-n auto` this module loads once per
# xdist worker and once in the controller, so this is one tally *per
# process* rather than one for the run -- `pytest_sessionfinish` and
# `pytest_testnodedown` below are what turn that into the single total
# rule 4 asks for.
_skip_counts = SkipCounts()

# how the skip of a target node that is not installed starts:
# `node-integration.yml`'s `bitcoind` job passes it as its
# `skip-reason-prefix`, so that job runs without either target and fails
# on any other skip
NO_TARGET = "no target node: "

# whether `--timeout` or `PYTEST_TIMEOUT` named a bound, read before
# `pytest_configure` writes its own into `config.option.timeout`
_CALLER_BOUND = pytest.StashKey[bool]()


@pytest.fixture(scope="session")
def skip_counts() -> SkipCounts:
    """Return this session's own tally, shared by every test that skips."""
    return _skip_counts


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Hand a worker's own tally to the controller, or report the total.

    Every one of the processes `-n auto` starts runs this hook; the body
    is `tests/conftest.py`'s own `stash_or_report`, which
    `[tool.coverage.run]`'s own `omit` cannot reach there, this module
    being under the omitted `tests/integration/*`.

    A fixture's own teardown print is captured with everything else a
    test writes and shown only where that test failed; this hook runs
    once its own process's session is over and is not subject to that
    capture -- rule 4's "the run prints a skip count" needs that, a
    green run that skipped every `Capability.MINE` case otherwise
    printing nothing at all.

    :param session: whose `.config` says whether this process is an
        xdist worker.
    :param exitstatus: unused; the hook's own signature names it.
    """
    del exitstatus
    stash_or_report(_skip_counts, getattr(session.config, "workeroutput", None))


def pytest_testnodedown(node: object, error: object | None) -> None:
    """Fold one xdist worker's own tally into the controller's, as it exits.

    xdist calls this hook only in the controller process, once per
    worker as it goes down (finishes or crashes) -- and, for every
    worker, before the controller reaches its own `pytest_sessionfinish`
    above, which is what reports the sum. Absent under `-n 0`, there
    being no worker to go down. The body is `tests/conftest.py`'s own
    `fold_worker_tally`.

    :param node: the worker that just finished; `node.workeroutput` is
        what `pytest_sessionfinish` above stashed in its own process,
        crossed over by xdist as plain data. Typed as `object` because
        `xdist.workermanage.WorkerController` ships no `py.typed`
        marker for this file's `strict = true` to check against.
    :param error: unused; the hook's own signature names it.
    """
    del error
    fold_worker_tally(_skip_counts, getattr(node, "workeroutput", None))


def pytest_addoption(parser: pytest.Parser) -> None:
    """Add Core's own `--tracerpc` and `--timeout-factor`, spelled Core's way.

    Only reached on an invocation naming `tests/integration` on the
    command line, pytest's initial-conftest collection running ahead of
    argument parsing over the paths given rather than over `testpaths`:
    measured live, a bare `uv run pytest --tracerpc` from the repository
    root answers "unrecognized arguments", where
    `uv run pytest tests/integration --tracerpc` does not. That matches
    every documented invocation of this module, which always names the
    path.
    """
    parser.addoption(
        "--tracerpc",
        action="store_true",
        default=False,
        help="print every RPC call this suite's adapters make, and its reply",
    )
    parser.addoption(
        "--timeout-factor",
        type=factor_from_option,
        default=1.0,
        help=(
            "scale every wait this suite's adapters and Peer make by this "
            "factor; 0 scales by 999, as Core's does, to disable them"
        ),
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Set this process's own `--timeout-factor`, before any node starts.

    One call per process, matching `_skip_counts`'s own scope above: an
    xdist worker runs its own `pytest_configure`, and every worker is
    handed the same command line, so every process scales the same way.

    The factor scales `[tool.pytest.ini_options]`'s own `timeout` too, a
    per-test bound that stayed put while the waits under it grew being
    what would fail a run the factor was given to let finish. It is
    scaled here, ahead of pytest-timeout's own `pytest_configure`, which
    reads `--timeout` before `PYTEST_TIMEOUT` before the ini value: a
    bound given either way is the caller's own and is left as given.
    Under xdist each worker re-parses the controller's command line
    rather than inheriting its option values, so it finds no `--timeout`,
    scales the ini value once itself, and reaches the same bound; a
    caller's `--timeout` is on that command line, so every process leaves
    it alone.

    :param config: the pytest session configuration.
    """
    set_factor(config.getoption("--timeout-factor"))
    caller_bound = config.getoption("timeout") is not None or (
        "PYTEST_TIMEOUT" in os.environ
    )
    config.stash[_CALLER_BOUND] = caller_bound
    ini_timeout = config.getini("timeout")
    if not caller_bound and ini_timeout:
        config.option.timeout = scaled(float(ini_timeout))


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Give a `scaled_timeout` test its own `timeout`, scaled like the ini one.

    A test carrying a Core wait at or past the ini `timeout` marks itself
    `pytest.mark.scaled_timeout(seconds)` and gets
    `pytest.mark.timeout(scaled(seconds))`: the wait's own `TimeoutError`
    can fire first, and the ratio between the two bounds is the same at
    every `--timeout-factor`. A bound of the caller's own is left to apply
    to every test, as `pytest_configure` leaves it.

    :param config: the pytest session configuration.
    :param items: the collected tests, marked in place.
    """
    if config.stash[_CALLER_BOUND]:
        return
    for item in items:
        marker = item.get_closest_marker("scaled_timeout")
        if marker is not None:
            item.add_marker(pytest.mark.timeout(scaled(float(marker.args[0]))))


# A line of a failure report past twice this keeps its first and last
# `_KEPT` characters. A GitHub runner stalls on a line of some megabytes,
# and a node that echoes the transaction it refused to decode makes one
# (issue btclib-org/functional-tests#442).
_KEPT = 1000


def _clipped(text: str) -> str:
    """Cut the middle out of every line of `text` longer than twice `_KEPT`."""
    return "\n".join(
        line
        if len(line) <= 2 * _KEPT
        else f"{line[:_KEPT]}... [{len(line) - 2 * _KEPT} cut] ...{line[-_KEPT:]}"
        for line in text.split("\n")
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Clip the lines of a failure report, whatever prints them.

    The traceback and the one-line summary both carry the exception's
    message whole; the terminal, the JUnit report and an xdist
    controller's copy all read this report.

    :param item: unused; the hook's own signature names it.
    :param call: unused; the hook's own signature names it.
    """
    del item, call
    report = yield
    longrepr: Any = report.longrepr
    chain = getattr(longrepr, "chain", None)
    if chain is None and hasattr(longrepr, "reprtraceback"):
        # `--tb=native` gives a representation with no chain
        chain = [(longrepr.reprtraceback, longrepr.reprcrash, None)]
    for traceback, crash, _ in chain or ():
        for entry in traceback.reprentries:
            entry.lines = [_clipped(line) for line in entry.lines]
        if crash is not None:
            crash.message = _clipped(crash.message)
    return report


@pytest.fixture(scope="session")
def make_adapter(request: pytest.FixtureRequest) -> AdapterFactory:
    """Return what every adapter a test builds is constructed through.

    `make_adapter(BitcoindAdapter, executable, datadir, rpc_port,
    p2p_port, ...)` in place of `BitcoindAdapter(executable, ...)`: the
    same positional and keyword arguments, with `trace_rpc` taken from
    `--tracerpc` rather than left at its default.
    `tests/tracerpc_reach_test.py` holds every construction under this
    directory to going through it or to naming its own `trace_rpc`.
    """
    return AdapterFactory(trace_rpc=bool(request.config.getoption("--tracerpc")))


def _require_integration() -> None:
    if not os.environ.get("TF2_INTEGRATION"):
        pytest.skip("set TF2_INTEGRATION=1 to run the integration tests")


@pytest.fixture(scope="session")
def bitcoind_path() -> str:
    """Return the bitcoind to run, skipping the whole module without one."""
    _require_integration()
    path = os.environ.get("TF2_BITCOIND") or shutil.which("bitcoind")
    if path is None:
        pytest.skip("no bitcoind: name one in TF2_BITCOIND or put it on PATH")
    return path


@pytest.fixture(scope="session")
def bitcoind_adapter(
    bitcoind_path: str,
    tmp_path_factory: pytest.TempPathFactory,
    make_adapter: AdapterFactory,
) -> Iterator[BitcoindAdapter]:
    """Yield a `BitcoindAdapter` over a regtest node started this session."""
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        BitcoindAdapter,
        bitcoind_path,
        tmp_path_factory.mktemp("bitcoind"),
        rpc_port,
        p2p_port,
    )
    adapter.start()
    try:
        yield adapter
    finally:
        adapter.stop()


@pytest.fixture
def bitcoind_cluster(
    bitcoind_path: str,
    tmp_path_factory: pytest.TempPathFactory,
    make_adapter: AdapterFactory,
) -> Iterator[Callable[[int], list[BitcoindAdapter]]]:
    """Yield a factory for `count` fresh `BitcoindAdapter`s, stopped after.

    Function-scoped, unlike `bitcoind_adapter` above: the first family's
    multi-node tests (`p2p_block_sync`, `p2p_compactblocks_hb`) each want
    their own clean-chain topology rather than one node shared across the
    whole session. `_cluster` below is the factory this yields.
    """
    yield from _cluster(
        make_adapter, BitcoindAdapter, bitcoind_path, tmp_path_factory, "bitcoind"
    )


def _cluster[A: NodeAdapter](
    make_adapter: AdapterFactory,
    cls: type[A],
    executable: str,
    tmp_path_factory: pytest.TempPathFactory,
    basename: str,
) -> Iterator[Callable[[int], list[A]]]:
    """Yield a cluster fixture's factory over `cls`, stopping every node after.

    A call starts `count` more nodes, each over a fresh data directory
    named from `basename` and a fresh pair of ports, and returns every node
    this factory has started so far, in the order it started them.
    """
    started: list[A] = []

    def _start(count: int) -> list[A]:
        for _ in range(count):
            rpc_port, p2p_port = free_ports(2)
            adapter = make_adapter(
                cls,
                executable,
                tmp_path_factory.mktemp(basename),
                rpc_port,
                p2p_port,
            )
            adapter.start()
            started.append(adapter)
        return started

    try:
        yield _start
    finally:
        stop_all(started)


@pytest.fixture(scope="session")
def btclib_node_python() -> str:
    """Return the interpreter to run btclib-node with, skipping without one.

    `TF2_BTCLIB_NODE_PYTHON` names it; `sys.executable` where unset. This
    project carries no dependency group installing `btclib-node` --
    `pyproject.toml`'s own comment on `[dependency-groups]` has the
    reasons. So `btclib-node` needs an interpreter of its own, installed
    into some other environment by hand, and named here rather than
    assumed. Probed by actually importing the package under that
    interpreter rather than by `shutil.which`: there is no console script
    this adapter runs (`btclib_node.py`'s own docstring is why), so
    nothing on `PATH` would ever answer for it.
    """
    _require_integration()
    python = os.environ.get("TF2_BTCLIB_NODE_PYTHON") or sys.executable
    probe = subprocess.run(  # noqa: S603
        [python, "-c", "import btclib_node"], check=False, capture_output=True
    )
    if probe.returncode != 0:
        pytest.skip(
            f"{NO_TARGET}no btclib_node importable by {python}: install it"
            " there, or name another interpreter in TF2_BTCLIB_NODE_PYTHON"
        )
    return python


@pytest.fixture(scope="session")
def btclib_node_adapter(
    btclib_node_python: str,
    tmp_path_factory: pytest.TempPathFactory,
    make_adapter: AdapterFactory,
) -> Iterator[BtclibNodeAdapter]:
    """Yield a `BtclibNodeAdapter`, a regtest node started this session."""
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        BtclibNodeAdapter,
        btclib_node_python,
        tmp_path_factory.mktemp("btclib-node"),
        rpc_port,
        p2p_port,
    )
    adapter.start()
    try:
        yield adapter
    finally:
        adapter.stop()


@pytest.fixture
def btclib_node_cluster(
    btclib_node_python: str,
    tmp_path_factory: pytest.TempPathFactory,
    make_adapter: AdapterFactory,
) -> Iterator[Callable[[int], list[BtclibNodeAdapter]]]:
    """Yield `bitcoind_cluster`'s factory, over `BtclibNodeAdapter`s instead.

    The same factory, so that one body taking a cluster runs against
    each node: the module docstring's own paragraph on one body over
    several nodes is the contract this fixture keeps.
    """
    yield from _cluster(
        make_adapter,
        BtclibNodeAdapter,
        btclib_node_python,
        tmp_path_factory,
        "btclib-node",
    )


@pytest.fixture
def mixed_cluster(
    bitcoind_path: str,
    btclib_node_python: str,
    tmp_path_factory: pytest.TempPathFactory,
    make_adapter: AdapterFactory,
) -> Iterator[tuple[BitcoindAdapter, BtclibNodeAdapter]]:
    """Yield one fresh `BitcoindAdapter` and one fresh `BtclibNodeAdapter`.

    [ISS 43](https://github.com/btclib-org/functional-tests/issues/43)'s
    own "most valuable case": a cluster of two different kinds of node,
    started independently and never wired together by this fixture --
    matching `bitcoind_cluster` above, a test's own `connect_nodes` is
    what joins them. Both fixtures this depends on skip the whole module
    without their own node, so a test naming this one skips the same way
    rather than starting only one side of the pair.

    Function-scoped like `bitcoind_cluster`, not session-scoped like
    either adapter fixture above: a fresh chain and a fresh pair of ports
    per test, the same reason `bitcoind_cluster` gives for its own.

    Both adapters are constructed, and so both draw their ports, before
    either starts -- unlike `bitcoind_cluster`'s own loop, which starts
    each node before the next one draws its ports. All four come from
    one `free_ports` call.
    """
    rpc_port1, p2p_port1, rpc_port2, p2p_port2 = free_ports(4)
    bitcoind = make_adapter(
        BitcoindAdapter,
        bitcoind_path,
        tmp_path_factory.mktemp("bitcoind"),
        rpc_port1,
        p2p_port1,
    )
    btclib_node = make_adapter(
        BtclibNodeAdapter,
        btclib_node_python,
        tmp_path_factory.mktemp("btclib-node"),
        rpc_port2,
        p2p_port2,
    )
    bitcoind.start()
    try:
        btclib_node.start()
    except BaseException:
        bitcoind.stop()
        raise
    try:
        yield bitcoind, btclib_node
    finally:
        try:
            btclib_node.stop()
        finally:
            bitcoind.stop()


@pytest.fixture(scope="session")
def btcd_path() -> str:
    """Return the btcd to run, skipping the whole module without one."""
    _require_integration()
    path = os.environ.get("TF2_BTCD") or shutil.which("btcd")
    if path is None:
        pytest.skip(f"{NO_TARGET}no btcd: name one in TF2_BTCD or put it on PATH")
    return path


@pytest.fixture(scope="session")
def btcd_adapter(
    btcd_path: str,
    tmp_path_factory: pytest.TempPathFactory,
    make_adapter: AdapterFactory,
) -> Iterator[BtcdAdapter]:
    """Yield a `BtcdAdapter` over a regtest node started this session."""
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        BtcdAdapter, btcd_path, tmp_path_factory.mktemp("btcd"), rpc_port, p2p_port
    )
    adapter.start()
    try:
        yield adapter
    finally:
        adapter.stop()


@pytest.fixture
def btcd_cluster(
    btcd_path: str,
    tmp_path_factory: pytest.TempPathFactory,
    make_adapter: AdapterFactory,
) -> Iterator[Callable[[int], list[BtcdAdapter]]]:
    """Yield `bitcoind_cluster`'s factory, over `BtcdAdapter`s instead."""
    yield from _cluster(make_adapter, BtcdAdapter, btcd_path, tmp_path_factory, "btcd")


@pytest.fixture
def btclib_wallets(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[BtclibWalletFactory]:
    """Yield the btclib-wallet side's factory, closing its wallets after.

    The wallet runs in this process, so no probe can tell a job that
    means to run it from one that does not: `TF2_BTCLIB_WALLET` is the
    switch, and without it the test skips as a target node that is not
    installed does.
    """
    _require_integration()
    if not os.environ.get("TF2_BTCLIB_WALLET"):
        pytest.skip(f"{NO_TARGET}set TF2_BTCLIB_WALLET=1 to run btclib-wallet")
    factory = BtclibWalletFactory(tmp_path_factory.mktemp("btclib-wallet"))
    try:
        yield factory
    finally:
        factory.close()
