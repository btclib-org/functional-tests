# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`NodeAdapter`'s own process and RPC plumbing, faked rather than spawned.

`_wait_for_rpc` is what makes a node's `start` non-trivial to get
covered honestly: it polls an RPC client and reads a process's own exit
code, neither of which needs a real node to exercise -- a fake of each,
matching the `Protocol`s `node.py` declares for them, drives every
branch.
"""

from __future__ import annotations

import errno
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Self, override
from unittest.mock import ANY, MagicMock, patch

import pytest
from bitcoin_core_rpc import HttpError, RpcError, RPCErrorCode

from btclib_functional_tests.capability import Capability
from btclib_functional_tests.node import (
    NodeAdapter,
    connect_nodes,
    disconnect_nodes,
    free_port,
    free_ports,
    sync_all,
    traced_transport,
    wait_until,
    wait_until_disconnected,
    wait_until_mempools_agree,
    wait_until_tips_agree,
)
from btclib_functional_tests.timeout_factor import set_factor

if TYPE_CHECKING:
    from collections.abc import Sequence
    from collections.abc import Set as AbstractSet
    from typing import IO
    from urllib.request import Request


class _FakeRpc:
    """A `bitcoin_core_rpc.BitcoinCoreRpcClient` stand-in: answers, or raises.

    `answers_after` is how many calls fail (a connection refused, in
    substance) before the next one succeeds -- `0` for a node that is up
    immediately, matching what `node_test.py`'s own callers ask for.
    """

    def __init__(self, answers_after: int = 0) -> None:
        self._remaining = answers_after
        self.calls: list[tuple[str, list[object] | None]] = []

    def call(self, method: str, params: list[object] | None = None) -> object:
        self.calls.append((method, params))
        if self._remaining > 0:
            self._remaining -= 1
            err_msg = "connection refused"
            raise ConnectionRefusedError(err_msg)
        return {"chain": "regtest"}


class _ScriptedRpc(_FakeRpc):
    """A `_FakeRpc` raising a caller-chosen exception rather than a fixed one.

    `answers_after` always raises `ConnectionRefusedError`; the
    `RpcError`/`HttpError` branches `_wait_for_rpc` gained need a
    caller-supplied exception, in order, before the node answers.
    """

    def __init__(self, raises: Sequence[BaseException]) -> None:
        super().__init__()
        self._raises = list(raises)

    @override
    def call(self, method: str, params: list[object] | None = None) -> object:
        self.calls.append((method, params))
        if self._raises:
            raise self._raises.pop(0)
        return {"chain": "regtest"}


class _FakeProcess:
    """A `subprocess.Popen` stand-in: `poll` answers `None` until it exits."""

    def __init__(self, exit_after: int | None = None) -> None:
        self._polls = 0
        self._exit_after = exit_after
        self.killed = False

    def poll(self) -> int | None:
        self._polls += 1
        if self._exit_after is not None and self._polls > self._exit_after:
            return 1
        return None

    def kill(self) -> None:
        self.killed = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return -9


class _FakeAdapter(NodeAdapter):
    """The smallest concrete `NodeAdapter`: a fake command and a fake client."""

    capabilities: AbstractSet[Capability] = frozenset()

    def __init__(self, *args: object, rpc: _FakeRpc, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._rpc = rpc

    @override
    def _command(self) -> list[str]:
        return ["fake-node", f"-datadir={self._datadir}"]

    @override
    def _rpc_client(self) -> _FakeRpc:  # type: ignore[override]
        return self._rpc


def test_free_port_returns_a_bindable_port() -> None:
    """The port `free_port` returns is free to bind at the moment it answers."""
    import socket  # noqa: PLC0415

    port = free_port()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_free_ports_returns_pairwise_distinct_bindable_ports() -> None:
    """Every port `free_ports` returns is free, and none repeats another."""
    import socket  # noqa: PLC0415

    ports = free_ports(3)
    assert len(set(ports)) == len(ports)
    for port in ports:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))


def test_free_ports_draws_below_every_ephemeral_range() -> None:
    """No port `free_ports` returns is one the kernel could hand out itself.

    [ISS 192](https://github.com/btclib-org/functional-tests/issues/192):
    a port inside Linux's default `ip_local_port_range`, which starts at
    32768, can be taken by any outgoing connection before the node binds
    it.
    """
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    assert node_module._PORTS.stop <= 32768
    assert all(port in node_module._PORTS for port in free_ports(3))


def _xdist(worker: str, count: int) -> dict[str, str]:
    """Return what `pytest-xdist` sets for worker `worker` of `count`."""
    return {"PYTEST_XDIST_WORKER": worker, "PYTEST_XDIST_WORKER_COUNT": str(count)}


def test_worker_block_is_the_whole_range_outside_xdist() -> None:
    """A process `pytest-xdist` did not start draws from all of `_PORTS`."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    assert node_module._worker_block({}) == node_module._PORTS


def test_worker_blocks_are_disjoint_slices_of_the_range() -> None:
    """No two workers of one run share a port, and none leaves `_PORTS`."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    blocks = [node_module._worker_block(_xdist(f"gw{i}", 4)) for i in range(4)]
    ports = [port for block in blocks for port in block]
    assert len(set(ports)) == len(ports)
    assert set(ports) <= set(node_module._PORTS)


def test_worker_block_is_the_whole_range_for_an_id_naming_no_number() -> None:
    """A worker named by `--tx popen//id=...` draws from all of `_PORTS`."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    assert node_module._worker_block(_xdist("custom", 4)) == node_module._PORTS


def test_worker_block_of_a_restarted_worker_wraps_onto_the_count() -> None:
    """A worker numbered past the count takes the block its number wraps to."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    restarted = node_module._worker_block(_xdist("gw5", 4))
    assert restarted == node_module._worker_block(_xdist("gw1", 4))


class _HeldSocket:
    """A `socket.socket` stand-in whose `bind` refuses every port in `held`."""

    held: ClassVar[set[int]] = set()

    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        del exc_info

    def bind(self, address: tuple[str, int]) -> None:
        if address[1] in _HeldSocket.held:
            raise OSError(errno.EADDRINUSE, "Address already in use")


@pytest.fixture
def held_ports(monkeypatch: pytest.MonkeyPatch) -> set[int]:
    """Replace `socket.socket` with `_HeldSocket`; return its `held` set."""
    monkeypatch.setattr("socket.socket", _HeldSocket)
    _HeldSocket.held.clear()
    return _HeldSocket.held


def test_port_cursor_skips_a_held_port_and_wraps_round(
    held_ports: set[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A port whose probe bind fails is skipped; the walk wraps at the end."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    monkeypatch.setattr("secrets.randbelow", lambda n: n - 1)
    held_ports.add(11)
    cursor = node_module._PortCursor(range(10, 14))
    assert cursor.take(3) == (13, 10, 12)


def test_port_cursor_does_not_repeat_across_calls(held_ports: set[int]) -> None:
    """Two draws of one port each answer two ports, not the same one twice.

    [ISS 85](https://github.com/btclib-org/functional-tests/issues/85)'s
    own defect: two sequential `free_port` calls answering one port.
    """
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    del held_ports
    cursor = node_module._PortCursor(range(10, 12))
    assert cursor.take(1) != cursor.take(1)


def test_port_cursor_skips_a_port_a_socket_listens_on() -> None:
    """A port an IPv4 socket listens on, as a node's does, fails the probe."""
    import socket  # noqa: PLC0415

    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = int(listener.getsockname()[1])
        cursor = node_module._PortCursor(range(port, port + 1))
        with pytest.raises(RuntimeError, match="0 of 1 ports free"):
            cursor.take(1)


def test_port_cursor_raises_where_a_lap_finds_too_few(held_ports: set[int]) -> None:
    """One lap of the block is the whole search, held ports and all."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    cursor = node_module._PortCursor(range(10, 12))
    with pytest.raises(RuntimeError, match="2 of 3 ports free"):
        cursor.take(3)
    held_ports.update({10, 11})
    with pytest.raises(RuntimeError, match="0 of 1 ports free"):
        cursor.take(1)


def test_traced_transport_prints_the_call_and_forwards_the_answer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--tracerpc`'s own wrapper prints both halves and changes nothing."""
    from urllib.request import Request  # noqa: PLC0415

    request = Request("http://127.0.0.1:1234", data=b'{"method": "getblockcount"}')

    def _inner_transport(req: Request, timeout: float) -> tuple[int, bytes]:
        assert req is request
        assert timeout == 5.0
        return 200, b'{"result": 1}'

    traced = traced_transport(_inner_transport)
    status, body = traced(request, 5.0)
    assert (status, body) == (200, b'{"result": 1}')
    printed = capsys.readouterr().out
    assert "getblockcount" in printed
    assert "200" in printed
    assert "result" in printed


def test_start_waits_for_the_rpc_and_creates_the_datadir(tmp_path: Path) -> None:
    """`start` creates the data directory and blocks until the RPC answers."""
    datadir = tmp_path / "node"
    rpc = _FakeRpc(answers_after=2)
    adapter = _FakeAdapter("fake-node", datadir, 0, 0, rpc=rpc)
    with patch("subprocess.Popen", return_value=_FakeProcess()) as popen:
        adapter.start()
    popen.assert_called_once_with(
        ["fake-node", f"-datadir={datadir}"], stdout=None, stderr=ANY
    )
    assert datadir.is_dir()
    assert rpc.calls == [("getblockchaininfo", None)] * 3


def test_start_appends_extra_args_after_the_command(tmp_path: Path) -> None:
    """`extra_args` follows `_command`'s own argv, naming no option of it."""
    datadir = tmp_path / "node"
    adapter = _FakeAdapter(
        "fake-node", datadir, 0, 0, extra_args=["-blocksdir=/elsewhere"], rpc=_FakeRpc()
    )
    with patch("subprocess.Popen", return_value=_FakeProcess()) as popen:
        adapter.start()
    popen.assert_called_once_with(
        ["fake-node", f"-datadir={datadir}", "-blocksdir=/elsewhere"],
        stdout=None,
        stderr=ANY,
    )


class _MultiOptionAdapter(_FakeAdapter):
    """A `_command` naming several option shapes, for the checks below."""

    @override
    def _command(self) -> list[str]:
        return ["fake-node", "-regtest", f"-datadir={self._datadir}", "--rpcport=0"]


@pytest.mark.parametrize(
    "extra_arg",
    [
        "-datadir=/elsewhere",
        "--datadir=/elsewhere",
        "-regtest",
        "-noregtest",
        "--noregtest",
        "-rpcport=1",
        "--rpcport=1",
    ],
)
def test_init_refuses_extra_args_naming_a_reserved_option(
    tmp_path: Path, extra_arg: str
) -> None:
    """Each shape `_command` could set an option in is refused the same way."""
    with pytest.raises(ValueError, match=r"^extra_args reuses -"):
        _MultiOptionAdapter(
            "fake-node", tmp_path / "node", 0, 0, extra_args=[extra_arg], rpc=_FakeRpc()
        )


@pytest.mark.parametrize(
    "extra_arg", ["-chain=main", "-testnet", "-testnet4", "--signet", "-nosignet"]
)
def test_init_refuses_every_chain_selector_once_the_command_names_one(
    tmp_path: Path, extra_arg: str
) -> None:
    """`-regtest` in `_command` reserves the other four spellings too."""
    with pytest.raises(ValueError, match=r"^extra_args reuses -"):
        _MultiOptionAdapter(
            "fake-node", tmp_path / "node", 0, 0, extra_args=[extra_arg], rpc=_FakeRpc()
        )


def test_init_accepts_a_chain_selector_where_the_command_names_none(
    tmp_path: Path,
) -> None:
    """The five are reserved as a group only where `_command` names one."""
    adapter = _FakeAdapter(
        "fake-node", tmp_path / "node", 0, 0, extra_args=["-testnet"], rpc=_FakeRpc()
    )
    assert adapter._extra_args == ("-testnet",)


def test_chain_is_regtest_where_the_caller_names_none(tmp_path: Path) -> None:
    """`chain` defaults to `regtest`, the one chain the base class names."""
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    assert adapter.chain == "regtest"
    assert NodeAdapter.chains == frozenset({"regtest"})


def test_init_refuses_a_chain_the_adapter_does_not_name(tmp_path: Path) -> None:
    """A chain outside `chains` is refused before `_command` is ever read."""
    with pytest.raises(ValueError, match=r"cannot start a node on chain 'main'"):
        _FakeAdapter("fake-node", tmp_path / "node", 0, 0, chain="main", rpc=_FakeRpc())


def test_init_accepts_a_chain_the_adapter_names(tmp_path: Path) -> None:
    """A subclass widening `chains` can be built on any chain it names."""

    class _MainAdapter(_FakeAdapter):
        chains: AbstractSet[str] = frozenset({"regtest", "main"})

    adapter = _MainAdapter(
        "fake-node", tmp_path / "node", 0, 0, chain="main", rpc=_FakeRpc()
    )
    assert adapter.chain == "main"


def test_init_accepts_extra_args_naming_no_reserved_option(tmp_path: Path) -> None:
    """An option `_command` never sets, or no option at all, passes through."""
    extra_args = ["-blocksdir=/elsewhere", "-no", "-", "positional"]
    adapter = _MultiOptionAdapter(
        "fake-node", tmp_path / "node", 0, 0, extra_args=extra_args, rpc=_FakeRpc()
    )
    assert adapter._extra_args == tuple(extra_args)


def test_start_raises_if_the_process_exits_first(tmp_path: Path) -> None:
    """A process gone before its RPC answers is a `RuntimeError`, not a hang."""
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with (
        patch("subprocess.Popen", return_value=_FakeProcess(exit_after=0)),
        pytest.raises(RuntimeError, match="exited with 1"),
    ):
        adapter.start()


def test_start_raises_carrying_a_real_process_s_own_stderr(tmp_path: Path) -> None:
    """A real, short-lived process's own stderr rides in the `RuntimeError`.

    A real `subprocess.Popen` rather than a mock: the capture this tests
    is the redirection `start` itself sets up, which a mocked `Popen`
    never exercises.
    """

    class _StderrAdapter(_FakeAdapter):
        @override
        def _command(self) -> list[str]:
            return [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('blocksdir missing'); sys.exit(1)",
            ]

    adapter = _StderrAdapter(
        sys.executable,
        tmp_path / "node",
        free_port(),
        free_port(),
        rpc=_FakeRpc(answers_after=10**6),
    )
    with pytest.raises(RuntimeError, match="exited with 1.*blocksdir missing"):
        adapter.start()


def test_start_raises_on_a_timeout(tmp_path: Path) -> None:
    """An RPC that never answers within the deadline is a `TimeoutError`."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    adapter = _FakeAdapter(
        "fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc(answers_after=10**6)
    )
    process = _FakeProcess()
    with (
        patch("subprocess.Popen", return_value=process),
        patch.object(node_module, "_STARTUP_TIMEOUT", 0.0),
        pytest.raises(TimeoutError, match="did not answer"),
    ):
        adapter.start()
    # nothing is left running: the process is killed and forgotten, so a
    # teardown's own `stop` after it has nothing to terminate
    assert process.killed
    adapter.stop()


def test_start_timeout_is_scaled_by_the_global_factor(tmp_path: Path) -> None:
    """A factor of 0 (`set_factor`) collapses even a real startup timeout."""
    adapter = _FakeAdapter(
        "fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc(answers_after=10**6)
    )
    set_factor(0.0)
    try:
        with (
            patch("subprocess.Popen", return_value=_FakeProcess()),
            pytest.raises(TimeoutError, match="did not answer"),
        ):
            adapter.start()
    finally:
        set_factor(1.0)


def test_start_timeout_chains_the_last_transient_rpc_failure(tmp_path: Path) -> None:
    """The `TimeoutError` carries what the node last answered, not `None`.

    Regression test for [ISS 98](https://github.com/btclib-org/functional-tests/issues/98):
    the pre-fix `_wait_for_rpc` raised a bare `TimeoutError`, so a caller's
    own `except ... as e: e.__cause__` read `None` for every timeout, a
    permanent answer included, and there was nothing in the traceback
    saying what the node had actually been sending back.
    """
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    rpc = _FakeRpc(answers_after=10**6)
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    with (
        patch("subprocess.Popen", return_value=_FakeProcess()),
        patch.object(node_module, "_STARTUP_TIMEOUT", 0.05),
        pytest.raises(TimeoutError, match="did not answer") as exc_info,
    ):
        adapter.start()
    assert isinstance(exc_info.value.__cause__, ConnectionRefusedError)


class _LoggingAdapter(_FakeAdapter):
    """A `_FakeAdapter` whose node logs to `<datadir>/node.log`."""

    @override
    def _log_path(self) -> Path:
        return self._datadir / "node.log"


class _QuietLoggingAdapter(_LoggingAdapter):
    """A `_LoggingAdapter` whose node prints its startup errors to stdout."""

    _discards_stdout = True


def test_start_timeout_carries_the_errors_stderr_and_this_start_s_log(
    tmp_path: Path,
) -> None:
    """The `TimeoutError` says what the node was doing, not only its deadline.

    Regression test for [ISS 134](https://github.com/btclib-org/functional-tests/issues/134):
    a timeout's message named its deadline alone. The line an earlier
    start left in the same log is not this start's, and stays out of the
    message.
    """
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    datadir = tmp_path / "node"
    datadir.mkdir()
    log = datadir / "node.log"
    log.write_text("an earlier start\n")

    def _popen(argv: list[str], *, stderr: IO[bytes], **_: object) -> _FakeProcess:
        del argv
        stderr.write(b"Warning: provoked\n")
        with log.open("a") as log_file:
            log_file.write("Binding RPC on address 127.0.0.1 port 1 failed.\n")
        return _FakeProcess()

    rpc = _FakeRpc(answers_after=10**6)
    adapter = _LoggingAdapter("fake-node", datadir, 0, 0, rpc=rpc)
    with (
        patch("subprocess.Popen", side_effect=_popen),
        patch.object(node_module, "_STARTUP_TIMEOUT", 0.05),
        pytest.raises(TimeoutError) as exc_info,
    ):
        adapter.start()
    message = str(exc_info.value)
    assert (
        f"(ignored errors: {{'ConnectionRefusedError': {len(rpc.calls)}}}, " in message
    )
    assert "latest: ConnectionRefusedError('connection refused'))" in message
    assert "-- stderr: Warning: provoked\n" in message
    assert message.endswith(
        f"-- {log} since this start:\nBinding RPC on address 127.0.0.1 port 1 failed."
    )
    assert "an earlier start" not in message


def test_start_early_exit_carries_this_start_s_log(tmp_path: Path) -> None:
    """A node whose stdout is discarded still says why it exited.

    btcd prints its log to stdout as well as to its log file, and its
    adapter discards stdout, so what it logged after opening the log
    reaches the error only through the file.
    """
    datadir = tmp_path / "node"
    datadir.mkdir()
    log = datadir / "node.log"
    log.write_text("an earlier start\n")

    def _popen(argv: list[str], **_: object) -> _FakeProcess:
        del argv
        with log.open("a") as log_file:
            log_file.write("Unable to start server\n")
        return _FakeProcess(exit_after=0)

    adapter = _QuietLoggingAdapter("fake-node", datadir, 0, 0, rpc=_FakeRpc())
    with (
        patch("subprocess.Popen", side_effect=_popen),
        pytest.raises(RuntimeError) as exc_info,
    ):
        adapter.start()
    assert str(exc_info.value) == (
        "node process exited with 1 before its RPC answered -- stderr: "
        f"\n-- {log} since this start:\nUnable to start server"
    )


def test_start_early_exit_keeps_stderr_alone_where_stdout_is_not_discarded(
    tmp_path: Path,
) -> None:
    """A node that gives its error on stderr is read by that text alone.

    Core's tests compare the message with the stderr text bitcoind gave, and
    bitcoind's log repeats it.
    """
    datadir = tmp_path / "node"
    datadir.mkdir()
    log = datadir / "node.log"

    def _popen(argv: list[str], *, stderr: IO[bytes], **_: object) -> _FakeProcess:
        del argv
        stderr.write(b"Error: provoked")
        log.write_text("Error: provoked\n")
        return _FakeProcess(exit_after=0)

    adapter = _LoggingAdapter("fake-node", datadir, 0, 0, rpc=_FakeRpc())
    with (
        patch("subprocess.Popen", side_effect=_popen),
        pytest.raises(RuntimeError) as exc_info,
    ):
        adapter.start()
    assert str(exc_info.value) == (
        "node process exited with 1 before its RPC answered -- stderr: Error: provoked"
    )


def test_start_timeout_reads_a_log_the_node_never_wrote_as_empty(
    tmp_path: Path,
) -> None:
    """A node stuck before opening its log leaves that part empty."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    datadir = tmp_path / "node"
    adapter = _LoggingAdapter("fake-node", datadir, 0, 0, rpc=_FakeRpc())
    with (
        patch("subprocess.Popen", return_value=_FakeProcess()),
        patch.object(node_module, "_STARTUP_TIMEOUT", 0.0),
        pytest.raises(TimeoutError) as exc_info,
    ):
        adapter.start()
    assert str(exc_info.value) == (
        "node did not answer its RPC within 0.0 s (ignored errors: {}) "
        f"-- stderr: \n-- {datadir / 'node.log'} since this start:\n"
    )


def test_start_timeout_names_no_log_where_the_adapter_knows_none(
    tmp_path: Path,
) -> None:
    """`NodeAdapter._log_path`'s own `None` leaves the log out."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with (
        patch("subprocess.Popen", return_value=_FakeProcess()),
        patch.object(node_module, "_STARTUP_TIMEOUT", 0.0),
        pytest.raises(TimeoutError) as exc_info,
    ):
        adapter.start()
    assert str(exc_info.value) == (
        "node did not answer its RPC within 0.0 s (ignored errors: {}) -- stderr: "
    )


def test_start_raises_at_once_on_an_rpc_error_that_is_not_warmup(
    tmp_path: Path,
) -> None:
    """A node answering with a real RPC error is failed at once, not waited out.

    Regression test for [ISS 98](https://github.com/btclib-org/functional-tests/issues/98):
    the pre-fix `_wait_for_rpc` caught every exception alike, so this waited
    out the whole startup timeout and was reported as `TimeoutError` --
    silence -- rather than as the answer the node actually sent.
    """
    error = RpcError("out of memory", RPCErrorCode.OUT_OF_MEMORY)
    rpc = _ScriptedRpc([error])
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    with (
        patch("subprocess.Popen", return_value=_FakeProcess()),
        pytest.raises(RuntimeError, match="answered its RPC with an error") as exc_info,
    ):
        adapter.start()
    assert exc_info.value.__cause__ is error
    assert rpc.calls == [("getblockchaininfo", None)]


def test_start_treats_rpc_in_warmup_as_still_starting(tmp_path: Path) -> None:
    """`-28 RPC_IN_WARMUP` is the `RpcError` code that is waited out.

    As Core's own `wait_for_rpc_connection`
    (`test_framework/test_node.py`) waits it out too.
    """
    error = RpcError("Loading block index...", RPCErrorCode.IN_WARMUP)
    rpc = _ScriptedRpc([error, error])
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    with patch("subprocess.Popen", return_value=_FakeProcess()):
        adapter.start()
    assert rpc.calls == [("getblockchaininfo", None)] * 3


@pytest.mark.parametrize("status", [401, 403])
def test_start_raises_at_once_on_an_http_401_or_403(
    tmp_path: Path, status: int
) -> None:
    """A credential no retry can fix is failed at once, not waited out."""
    error = HttpError("unauthorized", status)
    rpc = _ScriptedRpc([error])
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    with (
        patch("subprocess.Popen", return_value=_FakeProcess()),
        pytest.raises(RuntimeError, match="node refused its RPC") as exc_info,
    ):
        adapter.start()
    assert exc_info.value.__cause__ is error
    assert rpc.calls == [("getblockchaininfo", None)]


def test_start_waits_out_an_http_error_that_is_not_401_or_403(tmp_path: Path) -> None:
    """An `HttpError` other than 401/403 is waited out, not failed at once.

    A node's own startup can still resolve it on its own.
    """
    error = HttpError("service unavailable", 503)
    rpc = _ScriptedRpc([error])
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    with patch("subprocess.Popen", return_value=_FakeProcess()):
        adapter.start()
    assert rpc.calls == [("getblockchaininfo", None)] * 2


def _stderr_path(adapter: NodeAdapter) -> Path:
    """Return the file the process `adapter` holds writes its stderr to."""
    running = adapter._running
    assert running is not None
    return running.stderr_path


def test_stop_is_a_no_op_before_start(tmp_path: Path) -> None:
    """`stop` before `start` is a no-op, not a raise on a null process."""
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    adapter.stop()


def test_stop_terminates_and_waits(tmp_path: Path) -> None:
    """`stop` terminates the process and waits for it, then forgets it."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    adapter.stop()
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=ANY)
    adapter.stop()  # a second stop is again a no-op, the process forgotten


def test_stop_raises_on_a_process_that_already_exited_non_zero(
    tmp_path: Path,
) -> None:
    """A node gone before `stop` is a crash, raised with its code and stderr."""
    process = MagicMock()
    process.poll.side_effect = [None, 3]
    process.wait.return_value = 3
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    _stderr_path(adapter).write_bytes(b"Assertion failed: boom\n")
    with pytest.raises(
        RuntimeError,
        match=r"^node process exited with 3 before stop was called -- "
        r"stderr: Assertion failed: boom$",
    ):
        adapter.stop()
    adapter.stop()  # the crashed process is forgotten too


def test_stop_raises_on_a_non_zero_exit_on_terminate(tmp_path: Path) -> None:
    """A node exiting other than cleanly on `terminate` is raised too."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = -11
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    _stderr_path(adapter).write_bytes(b"")
    with pytest.raises(
        RuntimeError, match=r"^node process exited with -11 on terminate -- stderr: $"
    ):
        adapter.stop()
    process.terminate.assert_called_once()
    adapter.stop()


def test_stop_accepts_a_process_that_already_exited_cleanly(tmp_path: Path) -> None:
    """A node already gone with code 0, as an RPC `stop` leaves it, is clean."""
    process = MagicMock()
    process.poll.side_effect = [None, 0]
    process.wait.return_value = 0
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    adapter.stop()


def test_stop_accepts_stderr_beside_a_clean_exit(tmp_path: Path) -> None:
    """Stderr alone does not fail a node that exits cleanly on `terminate`."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    _stderr_path(adapter).write_bytes(b"Warning: provoked\n")
    adapter.stop()


def test_two_adapters_over_one_datadir_each_read_their_own_stderr(
    tmp_path: Path,
) -> None:
    """A second start over a running node's datadir leaves its stderr alone.

    Regression test for [ISS 105](https://github.com/btclib-org/functional-tests/issues/105):
    the second process's own stderr goes to a file of its own, so the
    first node's `stop` reads back what the first process wrote.
    """
    datadir = tmp_path / "node"
    written = iter([b"first\n", b"second\n"])

    def _popen(argv: list[str], *, stderr: IO[bytes], **_: object) -> MagicMock:
        del argv
        stderr.write(next(written))
        process = MagicMock()
        process.poll.return_value = None
        process.wait.return_value = 3
        return process

    first = _FakeAdapter("fake-node", datadir, 0, 0, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", datadir, 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", side_effect=_popen):
        first.start()
        second.start()
    assert _stderr_path(first) != _stderr_path(second)
    assert (
        _stderr_path(first).parent == _stderr_path(second).parent == datadir / "stderr"
    )
    with pytest.raises(RuntimeError, match=r"on terminate -- stderr: first$"):
        first.stop()
    with pytest.raises(RuntimeError, match=r"on terminate -- stderr: second$"):
        second.stop()


def test_start_refuses_a_second_start_while_a_process_is_held(tmp_path: Path) -> None:
    """A second `start` raises rather than orphaning the first process."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process) as popen:
        adapter.start()
        with pytest.raises(RuntimeError, match="^node already started"):
            adapter.start()
        assert popen.call_count == 1
        process.terminate.assert_not_called()
        adapter.stop()
        process.terminate.assert_called_once()
        adapter.start()  # a stopped adapter starts again
    assert popen.call_count == 2


def test_stop_kills_a_process_that_outlives_the_wait(tmp_path: Path) -> None:
    """A process still running after the wait is killed, then raised."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    process.wait.side_effect = [subprocess.TimeoutExpired("fake-node", 30.0), 0]
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    _stderr_path(adapter).write_bytes(b"still flushing\n")
    with pytest.raises(TimeoutError, match="killed -- stderr: still flushing"):
        adapter.stop()
    process.terminate.assert_called_once()
    process.kill.assert_called_once()
    assert process.wait.call_count == 2
    adapter.stop()  # the killed process is forgotten too


def test_stop_timeout_is_scaled_by_the_global_factor(tmp_path: Path) -> None:
    """`--timeout-factor` scales the wait for the process to exit too."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    set_factor(3.0)
    try:
        adapter.stop()
    finally:
        set_factor(1.0)
    startup_timeout = node_module._STARTUP_TIMEOUT
    process.wait.assert_called_once_with(timeout=3.0 * startup_timeout)


class _ScriptAdapter(_FakeAdapter):
    """A `_FakeAdapter` whose process is a real interpreter running `script`."""

    def __init__(self, tmp_path: Path, script: str) -> None:
        self._script = script
        super().__init__(sys.executable, tmp_path / "node", 0, 0, rpc=_FakeRpc())

    @override
    def _command(self) -> list[str]:
        return [sys.executable, "-c", self._script]


def test_wait_until_stopped_returns_the_exit_code_and_stderr(tmp_path: Path) -> None:
    """A process exiting on its own is waited for, then forgotten."""
    script = "import sys, time; time.sleep(0.5); sys.exit('Failure: clock')"
    adapter = _ScriptAdapter(tmp_path, script)
    adapter.start()
    sessions = adapter._sessions = MagicMock()
    assert adapter.wait_until_stopped() == (1, "Failure: clock")
    sessions.close.assert_called_once()
    adapter.stop()  # a no-op: the exit code 1 would raise here otherwise


def test_wait_until_stopped_reads_a_process_already_exited(tmp_path: Path) -> None:
    """A process that exited before the call is answered at once."""
    adapter = _ScriptAdapter(tmp_path, "import time; time.sleep(0.5)")
    adapter.start()
    running = adapter._running
    assert running is not None
    running.process.wait()
    assert adapter.wait_until_stopped(timeout=0.0) == (0, "")


def test_wait_until_stopped_raises_on_a_process_that_does_not_exit(
    tmp_path: Path,
) -> None:
    """A process still running at the deadline is raised on, and kept."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.side_effect = [subprocess.TimeoutExpired("fake-node", 3.0), 0]
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    _stderr_path(adapter).write_bytes(b"still running\n")
    set_factor(3.0)
    try:
        with pytest.raises(
            TimeoutError,
            match=r"^node process did not exit within 3.0 s -- stderr: still running$",
        ):
            adapter.wait_until_stopped(timeout=1.0)
    finally:
        set_factor(1.0)
    process.wait.assert_called_once_with(timeout=3.0)
    process.terminate.assert_not_called()
    adapter.stop()  # the process is still held, and `stop` ends it
    process.terminate.assert_called_once()


def test_wait_until_stopped_raises_where_no_process_is_held(tmp_path: Path) -> None:
    """Nothing started, nothing to wait for."""
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with pytest.raises(RuntimeError, match="^no node process to wait for"):
        adapter.wait_until_stopped()


def test_restart_stops_then_starts(tmp_path: Path) -> None:
    """`restart` is `stop` then `start`, over the same data directory."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process) as popen:
        adapter.start()
        adapter.restart()
    assert popen.call_count == 2
    process.terminate.assert_called_once()


def test_restart_extra_args_replace_the_constructor_s_for_that_start_only(
    tmp_path: Path,
) -> None:
    """Given `extra_args` apply to one start; the next falls back, as Core's."""
    datadir = tmp_path / "node"
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    adapter = _FakeAdapter(
        "fake-node", datadir, 0, 0, extra_args=["-uacomment=a"], rpc=_FakeRpc()
    )
    command = ["fake-node", f"-datadir={datadir}"]
    with patch("subprocess.Popen", return_value=process) as popen:
        adapter.start()
        adapter.restart(["-bantime=1234"])
        adapter.restart([])
        adapter.restart()
    assert [c.args[0] for c in popen.call_args_list] == [
        [*command, "-uacomment=a"],
        [*command, "-bantime=1234"],
        command,
        [*command, "-uacomment=a"],
    ]


def test_restart_refuses_a_reserved_option_before_stopping(tmp_path: Path) -> None:
    """A refused replacement leaves the running node running, untouched."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=process) as popen:
        adapter.start()
        with pytest.raises(ValueError, match=r"^extra_args reuses -datadir"):
            adapter.restart(["-datadir=/elsewhere"])
    assert popen.call_count == 1
    process.terminate.assert_not_called()


def test_restart_whose_start_fails_leaves_nothing_running(tmp_path: Path) -> None:
    """A node refusing its new argv is killed and forgotten; it starts again."""
    datadir = tmp_path / "node"
    running = MagicMock()
    running.poll.return_value = None
    running.wait.return_value = 0
    refused = _FakeProcess(exit_after=0)
    adapter = _FakeAdapter("fake-node", datadir, 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", side_effect=[running, refused, running]) as popen:
        adapter.start()
        with pytest.raises(RuntimeError, match="exited with 1"):
            adapter.restart(["-bantime=x"])
        assert refused.killed
        adapter.stop()  # nothing left to terminate
        running.terminate.assert_called_once()
        adapter.start()
    assert popen.call_args.args[0] == ["fake-node", f"-datadir={datadir}"]


def test_set_mock_time_calls_setmocktime(tmp_path: Path) -> None:
    """`set_mock_time` is `setmocktime`, the timestamp its own one argument."""
    rpc = _FakeRpc()
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    adapter.set_mock_time(1_700_000_000)
    assert rpc.calls == [("setmocktime", [1_700_000_000])]


def test_add_outbound_connection_calls_addconnection_over_v1(tmp_path: Path) -> None:
    """`add_outbound_connection` is `addconnection`, `v2transport` off."""
    rpc = _FakeRpc()
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    adapter.add_outbound_connection(("127.0.0.1", 4444), "addr-fetch")
    assert rpc.calls == [("addconnection", ["127.0.0.1:4444", "addr-fetch", False])]


def test_add_outbound_connection_passes_v2transport_on(tmp_path: Path) -> None:
    """`add_outbound_connection` passes `v2transport` on to `addconnection`."""
    rpc = _FakeRpc()
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 0, rpc=rpc)
    adapter.add_outbound_connection(("127.0.0.1", 4444), "addr-fetch", True)
    assert rpc.calls == [("addconnection", ["127.0.0.1:4444", "addr-fetch", True])]


def test_p2p_address_names_127_0_0_1_and_the_configured_port(tmp_path: Path) -> None:
    """`p2p_address` is `("127.0.0.1", p2p_port)`, the port this adapter got."""
    adapter = _FakeAdapter("fake-node", tmp_path / "node", 0, 4444, rpc=_FakeRpc())
    assert adapter.p2p_address == ("127.0.0.1", 4444)


def test_rpc_property_returns_a_fresh_client_each_time(tmp_path: Path) -> None:
    """`.rpc` calls `_rpc_client` again rather than caching one instance."""
    calls: list[_FakeRpc] = []

    class _CountingAdapter(_FakeAdapter):
        @override
        def _rpc_client(self) -> _FakeRpc:  # type: ignore[override]
            client = _FakeRpc()
            calls.append(client)
            return client

    adapter = _CountingAdapter("fake-node", tmp_path / "node", 0, 0, rpc=_FakeRpc())
    first = adapter.rpc
    second = adapter.rpc
    assert first is not second
    assert len(calls) == 2


def test_wait_until_returns_once_the_predicate_is_true() -> None:
    """The wait ends the moment `predicate` answers `True`, and not before."""
    calls: list[int] = []

    def _predicate() -> bool:
        calls.append(len(calls))
        return len(calls) >= 3

    wait_until(_predicate, timeout=30.0)
    assert len(calls) == 3


def test_wait_until_raises_on_a_timeout() -> None:
    """A deadline already past skips the loop, matching `connect_nodes`."""
    with pytest.raises(TimeoutError, match="condition not met"):
        wait_until(lambda: False, timeout=0.0)


def test_wait_until_timeout_is_scaled_by_the_global_factor() -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    set_factor(0.0)
    try:
        with pytest.raises(TimeoutError, match="condition not met"):
            wait_until(lambda: False, timeout=1000.0)
    finally:
        set_factor(1.0)


class _PeerInfoSequenceRpc(_FakeRpc):
    """Answers `getpeerinfo` with each of `answers` in turn, holding the last.

    Every other method answers `_FakeRpc`'s own default, `addnode`
    included -- this is a fake for `connect_nodes`'s own `getpeerinfo`
    polls, not for the one `addnode` call ahead of them.
    """

    def __init__(self, answers: list[object]) -> None:
        super().__init__()
        self._answers = answers
        self._index = 0

    @override
    def call(self, method: str, params: list[object] | None = None) -> object:
        super().call(method, params)
        if method != "getpeerinfo":
            return None
        answer = self._answers[min(self._index, len(self._answers) - 1)]
        self._index += 1
        return answer


def test_connect_nodes_addnodes_then_waits_on_both_sides_and_their_handshake(
    tmp_path: Path,
) -> None:
    """`connect_nodes` dials over `addnode onetry`, then waits three times.

    `first`'s own `getpeerinfo` answers a non-list once (an RPC hiccup,
    covering the branch a `list` answer never reaches), then a peer
    that is not the one dialled ahead of one that is -- `getpeerinfo`
    naming more than the one peer a call is about -- still short of a
    `pong`, then the same pair again past it: bitcoind's own shape, a
    peer short of `verack` listed regardless of state. `second`'s own
    answers no peer at all, then one -- btclib-node's own shape, a peer
    only ever listed once its handshake is already done, `pong` absent
    from an entry `_wait_for_handshake` reads as already complete.
    """
    address = "127.0.0.1:2222"
    first_rpc = _PeerInfoSequenceRpc(
        [
            {"chain": "regtest"},
            [
                {"id": 50, "addr": "127.0.0.1:5000", "inbound": False},
                {
                    "id": 7,
                    "addr": address,
                    "inbound": False,
                    "bytesrecv_per_msg": {"pong": 0},
                },
            ],
            [
                {"id": 99, "addr": "127.0.0.1:8888", "inbound": True},
                {
                    "id": 7,
                    "addr": address,
                    "inbound": False,
                    "bytesrecv_per_msg": {"pong": 29},
                },
            ],
        ]
    )
    second_rpc = _PeerInfoSequenceRpc(
        [
            [],  # before_second: nothing connected yet
            [],  # first poll after dialling: still nothing
            [{"id": 3, "addr": "127.0.0.1:9999", "inbound": True}],
        ]
    )
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=first_rpc)
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=second_rpc)
    connect_nodes(first, second)
    assert first_rpc.calls[0] == ("addnode", [address, "onetry", False])


def test_connect_nodes_raises_on_a_timeout(tmp_path: Path) -> None:
    """A connection that never shows on either side is a `TimeoutError`."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    with pytest.raises(TimeoutError, match="never reported a connection to"):
        connect_nodes(first, second, timeout=0.0)


def test_connect_nodes_timeout_is_scaled_by_the_global_factor(tmp_path: Path) -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    set_factor(0.0)
    try:
        with pytest.raises(TimeoutError, match="never reported a connection to"):
            connect_nodes(first, second, timeout=1000.0)
    finally:
        set_factor(1.0)


def test_connect_nodes_passes_v2transport_to_addnode(tmp_path: Path) -> None:
    """A caller asking for BIP324 gets `addnode`'s own `v2transport=True`."""
    first_rpc = _FakeRpc()
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=first_rpc)
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    set_factor(0.0)
    try:
        with pytest.raises(TimeoutError, match="never reported a connection to"):
            connect_nodes(first, second, v2transport=True)
    finally:
        set_factor(1.0)
    host, port = second.p2p_address
    assert first_rpc.calls[0] == ("addnode", [f"{host}:{port}", "onetry", True])


def test_connect_nodes_raises_where_only_second_never_shows_the_peer(
    tmp_path: Path,
) -> None:
    """`first` matching but `second` never showing it is `second`'s error."""
    address = "127.0.0.1:2222"
    first_rpc = _PeerInfoSequenceRpc([[{"id": 1, "addr": address, "inbound": False}]])
    second_rpc = _PeerInfoSequenceRpc([[]])
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=first_rpc)
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=second_rpc)
    with pytest.raises(TimeoutError, match="never reported a connection from"):
        connect_nodes(first, second, timeout=0.15)


def test_connect_nodes_raises_where_the_handshake_never_completes(
    tmp_path: Path,
) -> None:
    """Both sides showing the peer, but no `pong`, is its own `TimeoutError`.

    `first`'s own handshake wait sees a non-list answer once, then a
    peer that is not the one dialled, before settling on the one that
    is with its own `pong` stuck below the bound -- every shape a poll
    can answer without ever completing the handshake.
    """
    address = "127.0.0.1:2222"
    matching_peer = {
        "id": 1,
        "addr": address,
        "inbound": False,
        "bytesrecv_per_msg": {"pong": 0},
    }
    first_rpc = _PeerInfoSequenceRpc(
        [
            [matching_peer],  # first_peer's own match, for `_wait_for_peer`
            {"chain": "regtest"},  # handshake poll: not a list at all
            [{"id": 88, "addr": "127.0.0.1:7777", "inbound": True}],  # no match
            [matching_peer],  # held: matches, but its own `pong` never arrives
        ]
    )
    second_rpc = _PeerInfoSequenceRpc(
        [[], [{"id": 3, "addr": "127.0.0.1:9999", "inbound": True}]]
    )
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=first_rpc)
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=second_rpc)
    with pytest.raises(TimeoutError, match="never completed its own handshake"):
        connect_nodes(first, second, timeout=0.5)


def test_disconnect_nodes_disconnectnodes_and_waits_for_the_peer_to_go(
    tmp_path: Path,
) -> None:
    """`disconnect_nodes` calls `disconnectnode` and polls `getpeerinfo`.

    `getpeerinfo` still names the peer on its first poll, so the wait
    also covers the loop's own retry, matching
    `test_connect_nodes_addnodes_and_waits_for_a_connection` above.
    """

    class _PeerInfoRpc(_FakeRpc):
        def __init__(self) -> None:
            super().__init__()
            self._polls = 0

        @override
        def call(self, method: str, params: list[object] | None = None) -> object:
            super().call(method, params)
            if method == "getpeerinfo":
                self._polls += 1
                return [{"addr": "127.0.0.1:2222"}] if self._polls == 1 else []
            return None

    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_PeerInfoRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    disconnect_nodes(first, second)
    assert first._rpc.calls[0] == ("disconnectnode", ["127.0.0.1:2222"])


def test_disconnect_nodes_ignores_a_peer_at_a_different_address(
    tmp_path: Path,
) -> None:
    """A `getpeerinfo` entry naming another address does not block the wait."""

    class _OtherPeerRpc(_FakeRpc):
        @override
        def call(self, method: str, params: list[object] | None = None) -> object:
            super().call(method, params)
            return [{"addr": "127.0.0.1:9999"}] if method == "getpeerinfo" else None

    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_OtherPeerRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    disconnect_nodes(first, second)


def test_disconnect_nodes_raises_on_a_timeout(tmp_path: Path) -> None:
    """A deadline already past skips the loop, matching `connect_nodes`."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    with pytest.raises(TimeoutError, match="still reports a peer"):
        disconnect_nodes(first, second, timeout=0.0)


def test_disconnect_nodes_timeout_is_scaled_by_the_global_factor(
    tmp_path: Path,
) -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    set_factor(0.0)
    try:
        with pytest.raises(TimeoutError, match="still reports a peer"):
            disconnect_nodes(first, second, timeout=1000.0)
    finally:
        set_factor(1.0)


def test_wait_until_disconnected_timeout_is_scaled_by_the_global_factor(
    tmp_path: Path,
) -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    node = _FakeAdapter("fake-node", tmp_path / "node", 0, 1111, rpc=_FakeRpc())
    peer = _FakeAdapter("fake-node", tmp_path / "peer", 0, 2222, rpc=_FakeRpc())
    set_factor(0.0)
    try:
        with pytest.raises(TimeoutError, match="still reports a peer"):
            wait_until_disconnected(node, peer, timeout=1000.0)
    finally:
        set_factor(1.0)


def test_wait_until_mempools_agree_timeout_is_scaled_by_the_global_factor(
    tmp_path: Path,
) -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    set_factor(0.0)
    try:
        with pytest.raises(TimeoutError, match="one mempool"):
            wait_until_mempools_agree([first, second], timeout=1000.0)
    finally:
        set_factor(1.0)


def test_wait_until_tips_agree_returns_once_every_hash_matches(
    tmp_path: Path,
) -> None:
    """The wait ends the moment every node's `getbestblockhash` agrees."""

    class _TipRpc(_FakeRpc):
        def __init__(self, hashes: list[str]) -> None:
            super().__init__()
            self._hashes = iter(hashes)

        @override
        def call(self, method: str, params: list[object] | None = None) -> object:
            super().call(method, params)
            assert method == "getbestblockhash"
            return next(self._hashes)

    first = _FakeAdapter(
        "fake-node", tmp_path / "first", 0, 1111, rpc=_TipRpc(["a", "b"])
    )
    second = _FakeAdapter(
        "fake-node", tmp_path / "second", 0, 2222, rpc=_TipRpc(["b", "b"])
    )
    wait_until_tips_agree([first, second])


def test_wait_until_tips_agree_raises_on_a_timeout(tmp_path: Path) -> None:
    """A deadline already past skips the loop, matching `connect_nodes`."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    with pytest.raises(TimeoutError, match="did not converge"):
        wait_until_tips_agree([first, second], timeout=0.0)


def test_wait_until_tips_agree_timeout_is_scaled_by_the_global_factor(
    tmp_path: Path,
) -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    set_factor(0.0)
    try:
        with pytest.raises(TimeoutError, match="did not converge"):
            wait_until_tips_agree([first, second], timeout=1000.0)
    finally:
        set_factor(1.0)


def test_wait_until_disconnected_returns_once_the_peer_is_gone(
    tmp_path: Path,
) -> None:
    """The wait ends once `node`'s own `getpeerinfo` drops the peer's address.

    No `disconnectnode` call of any kind: unlike `disconnect_nodes`,
    this is the wait alone, for a drop triggered some other way.
    """

    class _PeerInfoRpc(_FakeRpc):
        def __init__(self) -> None:
            super().__init__()
            self._polls = 0

        @override
        def call(self, method: str, params: list[object] | None = None) -> object:
            super().call(method, params)
            assert method == "getpeerinfo"
            self._polls += 1
            return [{"addr": "127.0.0.1:2222"}] if self._polls == 1 else []

    node = _FakeAdapter("fake-node", tmp_path / "node", 0, 1111, rpc=_PeerInfoRpc())
    peer = _FakeAdapter("fake-node", tmp_path / "peer", 0, 2222, rpc=_FakeRpc())
    wait_until_disconnected(node, peer)
    assert node._rpc.calls == [("getpeerinfo", None)] * 2


def test_wait_until_disconnected_raises_on_a_timeout(tmp_path: Path) -> None:
    """A deadline already past skips the loop, matching `connect_nodes`."""
    node = _FakeAdapter("fake-node", tmp_path / "node", 0, 1111, rpc=_FakeRpc())
    peer = _FakeAdapter("fake-node", tmp_path / "peer", 0, 2222, rpc=_FakeRpc())
    with pytest.raises(TimeoutError, match="still reports a peer"):
        wait_until_disconnected(node, peer, timeout=0.0)


def test_wait_until_mempools_agree_returns_once_every_pool_matches(
    tmp_path: Path,
) -> None:
    """The wait ends the moment every node's `getrawmempool` set agrees."""

    class _MempoolRpc(_FakeRpc):
        def __init__(self, pools: list[list[str]]) -> None:
            super().__init__()
            self._pools = iter(pools)

        @override
        def call(self, method: str, params: list[object] | None = None) -> object:
            super().call(method, params)
            assert method == "getrawmempool"
            return next(self._pools)

    first = _FakeAdapter(
        "fake-node", tmp_path / "first", 0, 1111, rpc=_MempoolRpc([["a"], ["a", "b"]])
    )
    second = _FakeAdapter(
        "fake-node",
        tmp_path / "second",
        0,
        2222,
        rpc=_MempoolRpc([["a", "b"], ["a", "b"]]),
    )
    wait_until_mempools_agree([first, second])


def test_wait_until_mempools_agree_raises_on_a_timeout(tmp_path: Path) -> None:
    """A deadline already past skips the loop, matching `connect_nodes`."""
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    with pytest.raises(TimeoutError, match="did not converge on one mempool"):
        wait_until_mempools_agree([first, second], timeout=0.0)


def test_sync_all_waits_for_tips_then_for_mempools(tmp_path: Path) -> None:
    """`sync_all` waits for the tips first, then for the mempools."""

    class _SyncRpc(_FakeRpc):
        def __init__(self, hash_: str, pool: list[str]) -> None:
            super().__init__()
            self._hash = hash_
            self._pool = pool

        @override
        def call(self, method: str, params: list[object] | None = None) -> object:
            super().call(method, params)
            if method == "getbestblockhash":
                return self._hash
            assert method == "getrawmempool"
            return self._pool

    first = _FakeAdapter(
        "fake-node", tmp_path / "first", 0, 1111, rpc=_SyncRpc("a", ["x"])
    )
    second = _FakeAdapter(
        "fake-node", tmp_path / "second", 0, 2222, rpc=_SyncRpc("a", ["x"])
    )
    sync_all([first, second])


def test_sync_all_raises_where_the_tips_never_agree(tmp_path: Path) -> None:
    """A tip mismatch is `sync_all`'s own failure: the mempool wait never runs.

    `timeout=0.0` skips the loop before it ever calls `getbestblockhash`
    (matching `test_wait_until_tips_agree_raises_on_a_timeout`), so a
    plain `_FakeRpc` is enough: nothing here needs to answer for real.
    """
    first = _FakeAdapter("fake-node", tmp_path / "first", 0, 1111, rpc=_FakeRpc())
    second = _FakeAdapter("fake-node", tmp_path / "second", 0, 2222, rpc=_FakeRpc())
    with pytest.raises(TimeoutError, match="did not converge on one tip"):
        sync_all([first, second], timeout=0.0)


class _FakeMiniWallet:
    """`MiniWallet`'s own `generate`, answering fixed header hashes."""

    hashes = (b"\x01" * 32, b"\x02" * 32)

    def __init__(self, node: object) -> None:
        self.node = node

    def generate(self, count: int) -> list[bytes]:
        return list(self.hashes[:count])


class _FakeTipRpc(_FakeRpc):
    """`getbestblockhash` answering each of `tips` in turn, then the last."""

    def __init__(self, *tips: str) -> None:
        super().__init__()
        self.tips = list(tips)

    @override
    def call(self, method: str, params: list[object] | None = None) -> str:
        self.calls.append((method, params))
        return self.tips.pop(0) if len(self.tips) > 1 else self.tips[0]


def test_mine_returns_the_hashes_once_the_last_is_the_tip(tmp_path: Path) -> None:
    """`mine` polls `getbestblockhash` past a stale tip, then returns hex."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    rpc = _FakeTipRpc("01" * 32, "02" * 32)
    adapter = _FakeAdapter("fake-node", tmp_path, 0, 0, rpc=rpc)
    with patch.object(node_module, "MiniWallet", _FakeMiniWallet):
        hashes = adapter.mine(2)
    assert hashes == ["01" * 32, "02" * 32]
    assert rpc.calls == [("getbestblockhash", None)] * 2


def test_mine_zero_blocks_asks_the_node_nothing(tmp_path: Path) -> None:
    """`mine(0)` answers `[]` and polls no tip."""
    from btclib_functional_tests import node as node_module  # noqa: PLC0415

    rpc = _FakeTipRpc("00" * 32)
    adapter = _FakeAdapter("fake-node", tmp_path, 0, 0, rpc=rpc)
    with patch.object(node_module, "MiniWallet", _FakeMiniWallet):
        assert adapter.mine(0) == []
    assert rpc.calls == []


def test_start_sends_stdout_to_devnull_where_the_adapter_discards_it(
    tmp_path: Path,
) -> None:
    """`_discards_stdout` sends the process's stdout to `os.devnull`."""

    class _QuietAdapter(_FakeAdapter):
        _discards_stdout = True

    datadir = tmp_path / "node"
    adapter = _QuietAdapter("fake-node", datadir, 0, 0, rpc=_FakeRpc())
    with patch("subprocess.Popen", return_value=_FakeProcess()) as popen:
        adapter.start()
    popen.assert_called_once_with(
        ["fake-node", f"-datadir={datadir}"],
        stdout=subprocess.DEVNULL,
        stderr=ANY,
    )
