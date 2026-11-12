# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""What the whole suite shares: a hypothesis profile, a coverage gate.

The gate is `coverage_fail_under` and the guard on it. coverage looks
for its configuration in the directory the process started in, so a run
started from `tests/` finds no `fail_under`, no `source` and no
`branch = true`. Section 8 of the organization's standard leaves a tree
to point such a run at its configuration or to make it say it is
ungated, and this file is the second of the two: such a run is refused
(btclib-org/.github#443).

`stop_all`, `stash_or_report`, `fold_worker_tally` and `AdapterFactory`
are what `tests/integration/conftest.py`'s own hooks and fixtures call:
`[tool.coverage.run]`'s `omit` excludes that whole module, so their
bodies live here, in the one other file the "python tests naming" hook
lets sit beside `*_test.py` files without being one itself, and are
measured by an ordinary run (issue functional-tests#101).
"""

import os
from collections.abc import Generator, Mapping, Sequence
from pathlib import Path
from typing import Protocol, cast

import pytest
from hypothesis import settings

from btclib_functional_tests.capability import MissingCapabilityError, SkipCounts
from btclib_functional_tests.node import NodeAdapter

pytest_plugins = ["pytester"]

settings.register_profile("default", deadline=None, max_examples=500)
settings.register_profile("thorough", deadline=None, max_examples=2_000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_call(item: pytest.Item) -> Generator[None, object, object]:
    """Turn `capability.require`'s own exception into an actual skip.

    `capability.py` raises `MissingCapabilityError` rather than calling
    `pytest.skip` itself, so that importing it -- `sphinx-build`'s own
    `autodoc`, among others -- never needs `pytest` installed; this is
    the one place that exception meets a pytest session. An autouse
    fixture wrapping the test body in a `try`/`except` around its own
    `yield` does not do this: pytest's fixture teardown calls `next` on
    such a generator regardless of the test's own outcome, never
    `throw`, so the test's exception never reaches that `except` clause
    -- measured directly, a fixture of that shape read a raised
    `MissingCapabilityError` as an ordinary failure, not a skip. A
    `pytest_runtest_call` hookwrapper is what actually sees the test's
    own exception, in `outcome.get_result()`'s propagation through the
    `yield` below, because pytest calls exactly this hook to run the
    test in the first place.

    :param item: unused; the hook's own signature names it.
    :returns: the wrapped call's own result, forwarded unchanged.
    """
    del item
    try:
        return (yield)
    except MissingCapabilityError as exc:
        pytest.skip(str(exc))


def asks_for_everything(
    file_or_dir: list[str] | None, testpaths: list[str], rootpath: Path
) -> bool:
    """Whether the paths named on the command line take the suite in.

    No path at all narrows nothing and is the whole run. A path above one
    of them -- `pytest .`, or the rootdir spelled out -- collects it too,
    so what decides is containment and not equality.

    :param file_or_dir: the paths given on the command line, or `None`
        on the `--help` path, where argument parsing was abandoned.
    :param testpaths: the configured test paths, relative to the rootdir.
    :param rootpath: the pytest rootdir.
    :returns: whether the run collects the whole suite.
    """
    if not (given := [Path(path).resolve() for path in file_or_dir or []]):
        return True
    wanted = [(rootpath / path).resolve() for path in testpaths]
    return bool(wanted) and all(
        any(target == path or path in target.parents for path in given)
        for target in wanted
    )


def coverage_fail_under(
    asked: float | None,
    configured: float | None,
    file_or_dir: list[str] | None,
    keyword: str,
    markexpr: str,
    deselect: list[str] | None,
    ignore: list[str] | None,
    ignore_glob: list[str] | None,
    lf: bool,
    testpaths: list[str],
    rootpath: Path,
) -> float | None:
    """Return the coverage threshold this run's selection has to meet.

    A run that leaves tests out measures the same source with fewer
    tests, so its report is gated at zero instead of at `fail_under`,
    which a whole run is handed back untouched.

    :param asked: an explicit `--cov-fail-under`, honoured either way.
    :param configured: `fail_under` as pytest-cov read it from
        `pyproject.toml`.
    :param file_or_dir: the paths given on the command line.
    :param keyword: `-k`.
    :param markexpr: `-m`.
    :param deselect: `--deselect`.
    :param ignore: `--ignore`.
    :param ignore_glob: `--ignore-glob`.
    :param lf: `--lf`.
    :param testpaths: the configured test paths.
    :param rootpath: the pytest rootdir.
    :returns: the threshold this run is held to.
    """
    if asked is not None:
        return asked
    if keyword or markexpr or deselect or ignore or ignore_glob or lf:
        return 0
    if not asks_for_everything(file_or_dir, testpaths, rootpath):
        return 0
    return configured


class CoverageConfiguration(Protocol):
    """What this file reads of coverage's own configuration object."""

    config_file: str | None


def coverage_configuration(config: pytest.Config) -> CoverageConfiguration | None:
    """Return the configuration coverage is measuring with, or `None`.

    :param config: the pytest session configuration.
    :returns: the configuration object, or `None` where `--no-cov` was
        given or the plugin never registered.
    """
    plugin = config.pluginmanager.getplugin("_cov")
    controller = getattr(plugin, "cov_controller", None)
    return (
        None
        if controller is None
        else cast("CoverageConfiguration", controller.cov.config)
    )


def configuration_went_unread(
    cov_config: CoverageConfiguration | None,
    inipath: Path | None,
    asked: float | None,
    asked_for_help: bool,
    collect_only: bool,
) -> bool:
    """Return whether a run held to the floor cannot see one.

    :param cov_config: what `coverage_configuration` answered.
    :param inipath: the configuration file pytest read.
    :param asked: an explicit `--cov-fail-under`.
    :param asked_for_help: whether `--help` was given.
    :param collect_only: whether `--collect-only` was given.
    :returns: whether the floor is unreachable for this run.
    """
    if cov_config is None:
        return False
    if asked is not None:
        return False
    if asked_for_help or collect_only:
        return False
    return cov_config.config_file is None and inipath is not None


def pytest_configure(config: pytest.Config) -> None:
    """Gate a whole run at `fail_under`, and a partial one at nothing.

    :param config: the pytest session configuration.
    :raises pytest.UsageError: where coverage read no configuration.
    """
    if configuration_went_unread(
        coverage_configuration(config),
        config.inipath,
        config.option.cov_fail_under,
        config.option.help,
        config.option.collectonly,
    ):
        raise pytest.UsageError(
            "coverage read no configuration, so this run is held to no floor"
            " and measures a different set of files: coverage looks only in"
            f" the directory the run started in, {Path.cwd()}, and pytest"
            f" read {config.inipath}. Run from {config.rootpath};"
            " --cov-config restores the floor and not the file set, a"
            " relative omit pattern being resolved against the directory"
            " the run started in."
        )
    namespace = config.known_args_namespace
    namespace.cov_fail_under = coverage_fail_under(
        config.option.cov_fail_under,
        namespace.cov_fail_under,
        config.option.file_or_dir,
        config.option.keyword,
        config.option.markexpr,
        config.option.deselect,
        config.option.ignore,
        config.option.ignore_glob,
        getattr(config.option, "lf", False),
        config.getini("testpaths"),
        config.rootpath,
    )


class Stoppable(Protocol):
    """What `stop_all` needs of an adapter: `NodeAdapter.stop`, structurally.

    A `Protocol` rather than `btclib_functional_tests.node.NodeAdapter` itself,
    so a test can stand one up without a real `NodeAdapter` subclass.
    """

    def stop(self) -> None:
        """Stop this adapter; `NodeAdapter.stop`'s own contract."""


def stop_all(adapters: Sequence[Stoppable]) -> None:
    """Stop every one of `adapters`, last started first.

    `tests/integration/conftest.py`'s own `_cluster`, behind every
    `<node>_cluster` fixture, calls this from its teardown. Nested
    `try`/`finally` rather than a `contextlib.ExitStack`: each stop runs
    even where a later-started one raised -- a node `stop` had to kill, or
    one that had crashed -- and every error raised is kept, each chained
    to the one before, where an `ExitStack` runs its callbacks outside an
    `except` block and keeps only the last error it meets.

    :param adapters: the adapters to stop, in the order they were
        started.
    """
    if not adapters:
        return
    try:
        adapters[-1].stop()
    finally:
        stop_all(adapters[:-1])


def stash_or_report(
    skip_counts: SkipCounts, workeroutput: dict[str, object] | None
) -> None:
    """Stash this process's own tally for the controller, or print the total.

    `tests/integration/conftest.py`'s own `pytest_sessionfinish` calls
    this with `session.config.workeroutput`, which exists only inside an
    xdist worker (`xdist.remote` sets it at worker start-up), never in
    the controller and never in a plain `-n 0` run -- a worker stashes
    its own tally there instead of printing it, and `fold_worker_tally`
    below is what the controller reads it back through, before this same
    function prints the sum on the controller's own process. A plain
    `-n 0` run is not a worker either, and needs no folding: it is the
    one process that ran every test, so its own tally is already the
    whole run's.

    :param skip_counts: this process's own running tally.
    :param workeroutput: `session.config.workeroutput`, or `None` outside
        an xdist worker.
    """
    if workeroutput is not None:
        workeroutput["skip_counts"] = skip_counts.as_mapping()
        return
    print(skip_counts.report())  # noqa: T201


def fold_worker_tally(
    skip_counts: SkipCounts, workeroutput: Mapping[str, object] | None
) -> None:
    """Add one xdist worker's own tally into `skip_counts`, as it exits.

    `tests/integration/conftest.py`'s own `pytest_testnodedown` calls this
    with `node.workeroutput`. xdist calls that hook only in the controller
    process, once per worker as it goes down (finishes or crashes) -- and,
    for every worker, before the controller reaches its own
    `stash_or_report` above, which is what reports the sum. Never called
    under `-n 0`, there being no worker to go down.

    :param skip_counts: the controller's own running tally.
    :param workeroutput: what that worker's own `stash_or_report` stashed
        in its process, crossed over by xdist as plain data, or `None`
        where the worker never got that far.
    """
    if workeroutput is not None:
        skip_counts.add_mapping(
            cast("Mapping[str, int]", workeroutput.get("skip_counts", {}))
        )


class AdapterFactory:
    """Construct any `NodeAdapter` subclass with this session's `--tracerpc`.

    `tests/integration/conftest.py`'s own `make_adapter` fixture returns
    one. `NodeAdapter` takes `trace_rpc` per instance rather than as a
    global, so the option reaches an adapter only through its
    constructor, and an adapter a test builds without this factory or a
    `trace_rpc` of its own never traces.

    :param trace_rpc: whether `--tracerpc` was given.
    """

    def __init__(self, *, trace_rpc: bool) -> None:
        self._trace_rpc = trace_rpc

    def __call__[A: NodeAdapter](
        self,
        cls: type[A],
        executable: str,
        datadir: Path,
        rpc_port: int,
        p2p_port: int,
        extra_args: Sequence[str] = (),
        rpc_auth: tuple[str, str] | None = None,
        *,
        chain: str = "regtest",
    ) -> A:
        """Return `cls(...)`, its `trace_rpc` taken from `--tracerpc`.

        :param cls: the adapter class to construct, a test module's own
            subclass included.
        :param chain: `NodeAdapter`'s own `chain`, passed through.
        :returns: the constructed adapter, not yet started.
        """
        return cls(
            executable,
            datadir,
            rpc_port,
            p2p_port,
            extra_args,
            rpc_auth,
            trace_rpc=self._trace_rpc,
            chain=chain,
        )
