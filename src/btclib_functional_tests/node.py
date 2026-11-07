# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`NodeAdapter`: how a node starts, stops and restarts, and its RPC.

Step 3 of [ISS 2220](https://github.com/btclib-org/btclib/issues/2220):
one base class over what every adapter shares --
spawning a process, waiting for its RPC to answer, and tearing it down
again -- with each subclass supplying only what differs: the command
line an option is spelled with, and how RPC authenticates (rule 1 of
that issue names both as the adapter's own).

Reached only over a process and an RPC socket, never in-process: this
module imports no node, `subprocess.Popen` is the whole of how one
starts, and `bitcoin_core_rpc.BitcoinCoreRpcClient` is the whole of how
one answers.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import tempfile
import threading
import time
import weakref
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Sequence
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple, Protocol
from urllib.request import Request

from bitcoin_core_rpc import BitcoinCoreRpcClient, HttpError, RpcError, RPCErrorCode
from bitcoin_core_rpc.transport import HttpTransport, SessionTransport

from btclib_functional_tests.mini_wallet import MiniWallet
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from collections.abc import Set as AbstractSet

    from btclib_functional_tests.capability import Capability

__all__ = [
    "NodeAdapter",
    "connect_nodes",
    "disconnect_nodes",
    "free_port",
    "free_ports",
    "sync_all",
    "traced_transport",
    "wait_until",
    "wait_until_disconnected",
    "wait_until_mempools_agree",
    "wait_until_tips_agree",
]

# how long a freshly spawned node is given to answer its first RPC call:
# a regtest node of either kind is up in well under a second on any
# machine that can run this suite, so this bounds the failure case
_STARTUP_TIMEOUT = 30.0

# how long `wait_until_stopped` waits by default: Core's own
# `BITCOIND_PROC_WAIT_TIMEOUT` (`test_framework/test_node.py`)
_PROC_WAIT_TIMEOUT = 60.0

# the directory inside a datadir where each `start` creates a file of its
# own for the process's stderr: Core's own `stderr_dir`
# (`TestNode.__init__`), which `initialize_datadir` (`util.py`) creates
_STDERR_DIR = "stderr"

# the exit code `stop` accepts, for every adapter: Core's own
# `TestNode.stop_node` expects 0, and each node exits 0 on `terminate`'s
# SIGTERM -- measured on the bitcoind release `node-integration.yml`
# pins, btclib-node 2026.9.24 and btclib-node's `main` at f8f7143
_CLEAN_EXIT = 0

# every option naming the chain a node runs: Core's `-chain=` and the four
# flags each equivalent to one of its values (`SetupChainParamsBaseOptions`,
# `src/chainparamsbase.cpp`)
_CHAIN_SELECTORS = frozenset({"chain", "regtest", "testnet", "testnet4", "signet"})

# the chain `NodeAdapter` starts on where the caller names none
_DEFAULT_CHAIN = "regtest"

# the ports `free_ports` draws from: past every port Core's own
# `p2p_port`, `rpc_port` and `tor_port` (`test_framework/util.py`)
# return at their default `PORT_MIN`, and below the first port the
# kernel picks a local port from -- 32768 in Linux's default
# `ip_local_port_range` (`Documentation/networking/ip-sysctl.rst`), 49152
# in macOS's `net.inet.ip.portrange.first`
_PORTS = range(26000, 32768)


def free_port() -> int:
    """Return one port a bind on `127.0.0.1` accepts, as `free_ports` does."""
    return free_ports(1)[0]


def free_ports(count: int) -> tuple[int, ...]:
    """Return `count` ports a bind on `127.0.0.1` accepts, pairwise distinct.

    Drawn from `_PORTS` rather than from the kernel's own pick: a port
    the kernel hands out is one it can hand out again, to any other
    process's `bind` to port `0` or outgoing connection, between this
    call returning and the caller's node binding it
    ([ISS 192](https://github.com/btclib-org/functional-tests/issues/192)).

    Each process draws from a block of its own, `_worker_block`'s, the
    way Core's own `p2p_port` (`test_framework/util.py`) offsets each
    test process by its `PortSeed`, so no two of the numbered workers
    `pytest-xdist -n` starts together draw the same port. Within the block,
    `_PortCursor` walks on from where its last draw stopped, and a
    candidate another process holds on `127.0.0.1` or on every IPv4
    address -- another run of this suite included -- fails its probe bind
    and is skipped; one held on IPv6 alone does not.

    :param count: how many ports to return.
    :raises RuntimeError: fewer than `count` ports of this process's
        block were free.
    """
    return _process_cursor().take(count)


def _worker_block(environ: Mapping[str, str]) -> range:
    """Return the slice of `_PORTS` this process draws its ports from.

    `pytest-xdist` numbers each worker in `PYTEST_XDIST_WORKER` (`gw0`,
    `gw1`, ...) and says how many there are in
    `PYTEST_XDIST_WORKER_COUNT` (`xdist/remote.py`), and `_PORTS` is cut
    into that many equal blocks. A worker restarted after a crash is
    numbered past the count, and takes the block its number wraps onto,
    which a live worker may still be drawing from. A process outside
    `pytest-xdist`, or one whose id names no number (`--tx
    popen//id=...`), has the whole of `_PORTS`.

    :param environ: the process environment, `os.environ`.
    """
    worker = environ.get("PYTEST_XDIST_WORKER", "").removeprefix("gw")
    if not worker.isdecimal():
        return _PORTS
    workers = int(environ.get("PYTEST_XDIST_WORKER_COUNT", "1"))
    size = len(_PORTS) // workers
    index = int(worker) % workers
    return _PORTS[index * size : (index + 1) * size]


class _PortCursor:
    """A walk over one block of ports, from a random start, wrapping round.

    The random start is for two runs of this suite side by side on one
    machine, whose workers of the same number share a block: each run
    starts its walk somewhere else in it.
    """

    def __init__(self, block: range) -> None:
        self._block = block
        self._next = secrets.randbelow(len(block))

    def take(self, count: int) -> tuple[int, ...]:
        """Return the next `count` ports of the block a probe bind accepts.

        At most one lap of the block per call, so no port repeats within
        one answer, and a port answered is not visited again until the
        walk has come round the whole block.

        :param count: how many ports to return.
        :raises RuntimeError: the lap ended with fewer than `count` found.
        """
        ports: list[int] = []
        for _ in self._block:
            if len(ports) == count:
                break
            port = self._block[self._next]
            self._next = (self._next + 1) % len(self._block)
            with socket.socket() as probe:
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
            ports.append(port)
        if len(ports) < count:
            err_msg = f"{len(ports)} of {count} ports free in {self._block}"
            raise RuntimeError(err_msg)
        return tuple(ports)


@cache
def _process_cursor() -> _PortCursor:
    """Return this process's own cursor, built on its first draw."""
    return _PortCursor(_worker_block(os.environ))


def traced_transport(transport: HttpTransport) -> HttpTransport:
    """Wrap `transport`, printing every RPC exchange it carries.

    Core's own `--tracerpc` (`test_framework.py`): "Print out all RPC
    calls as they are made". `bitcoin_core_rpc.BitcoinCoreRpcClient`'s
    own `transport=` is exactly the two-argument callable
    (`bitcoin_core_rpc.transport.HttpTransport`) this wraps, an
    already-built `Request` and a timeout answered with a status and a
    body, so tracing needs no change to the client itself.

    :param transport: the transport to wrap, an adapter's own
        `_ThreadSessions` where `NodeAdapter._rpc_transport` is what asks.
    """

    def _traced(request: Request, timeout: float) -> tuple[int, bytes]:
        print(f"--> {request.full_url} {request.data!r}")  # noqa: T201
        status, body = transport(request, timeout)
        print(f"<-- {status} {body!r}")  # noqa: T201
        return status, body

    return _traced


class _ThreadEnd:
    """Held by one thread's `threading.local` alone, freed as it ends."""

    __slots__ = ("__weakref__",)


class _ThreadSessions:
    """An `HttpTransport` keeping one `SessionTransport` per calling thread.

    `bitcoin_core_rpc.transport.urlopen_transport` opens a connection per
    call and the node closes it, leaving one socket in `TIME_WAIT` on the
    node's port per call
    ([ISS 278](https://github.com/btclib-org/functional-tests/issues/278));
    a `SessionTransport` keeps one open across calls instead.

    One per thread rather than one for the adapter: a `SessionTransport`
    serializes every call made through it under one lock, so threads
    sharing one would never have two calls in flight at once, which is
    the subject of `rpc_echo_payload_test.py`, whose callers have to fill
    the node's own work queue. Which thread's is looked up on each call,
    not when a client is built, so a client built in one thread and
    called from another still uses the caller's.

    A thread's `SessionTransport` is closed and dropped when the thread
    ends: a `_ThreadEnd` only that thread's `threading.local` holds is
    released then, and its finalizer is what closes it. bitcoind keeps an
    idle connection open for its `-rpcservertimeout`, which `bitcoind.py`
    sets past any test's length, and a build of Core's `master` accepts no
    connection past `-rpcmaxconnections`, `DEFAULT_MAX_HTTP_CONNECTIONS`
    (`src/httpserver.h`) by default: a connection kept for a thread that
    no longer runs would hold one of those for the node's whole life.
    `p2p_private_broadcast_test.py` is the test that needs this, its
    SOCKS5 proxy calling the node from a thread of its own per proxied
    connection
    ([ISS 336](https://github.com/btclib-org/functional-tests/issues/336)).

    `close` closes every connection each of them holds, and leaves each
    usable: the next call opens a connection afresh.

    :param factory: what builds each thread's `SessionTransport`, the
        seam a test replaces.
    """

    def __init__(
        self, factory: Callable[[], SessionTransport] = SessionTransport
    ) -> None:
        self._factory = factory
        self._local = threading.local()
        self._lock = threading.Lock()
        self._sessions: list[SessionTransport] = []

    def __call__(self, request: Request, timeout: float) -> tuple[int, bytes]:
        """Send `request` over the calling thread's own `SessionTransport`."""
        session: SessionTransport | None = getattr(self._local, "session", None)
        if session is None:
            session = self._factory()
            self._local.session = session
            self._local.end = end = _ThreadEnd()
            with self._lock:
                self._sessions.append(session)
            weakref.finalize(end, self._retire, session)
        return session(request, timeout)

    def _retire(self, session: SessionTransport) -> None:
        """Close and drop the `SessionTransport` of a thread that ended."""
        with self._lock:
            self._sessions.remove(session)
        session.close()

    def close(self) -> None:
        """Close every connection any thread's `SessionTransport` holds."""
        with self._lock:
            sessions = list(self._sessions)
        for session in sessions:
            session.close()


class _RpcProbe(Protocol):
    """What `_wait_for_rpc` needs of an RPC client: one call, or a raise."""

    def call(self, method: str, params: list[object] | None = None) -> object: ...


class _Process(Protocol):
    """What `_wait_for_rpc` needs of a process: has it already exited."""

    def poll(self) -> int | None: ...


def _option_name(token: str) -> str | None:
    """Return the bare option name `token` spells, or `None` if it spells none.

    `-opt`, `--opt`, `-opt=value` and `--opt=value` all name `opt`; a
    token carrying no leading dash -- an executable path, a bare
    positional -- names nothing, and neither does a lone `-`.

    :param token: one argv entry.
    """
    if not token.startswith("-"):
        return None
    name = token.lstrip("-").split("=", 1)[0]
    return name or None


def _canonical_option_name(name: str) -> str:
    """Collapse Core's own negated spelling onto the option it negates.

    bitcoind treats `-noopt` as `-opt=0`
    (`ArgsManager::GetBoolArg`, `src/util/args.cpp`), so `noopt` and
    `opt` name the same knob and compare equal here.

    :param name: an option name, as `_option_name` returns it.
    """
    if name.startswith("no") and len(name) > len("no"):
        return name[len("no") :]
    return name


def _reserved_option_names(command: Sequence[str]) -> frozenset[str]:
    """Return every option name `command` itself sets, canonicalized.

    Read out of the adapter's own `_command()` rather than kept by hand
    beside it: an argument `_command` grows is reserved from the commit
    that adds it, with nothing else to update.

    A command naming one of `_CHAIN_SELECTORS` reserves all of them: they
    are five spellings of one setting, and bitcoind refuses a command line
    setting more than one (`ArgsManager::GetChainArg`,
    `src/common/args.cpp`: "Can use at most one").

    :param command: an adapter's own argv, `_command()`'s answer.
    """
    names = (_option_name(token) for token in command)
    reserved = frozenset(
        _canonical_option_name(name) for name in names if name is not None
    )
    if reserved & _CHAIN_SELECTORS:
        return reserved | _CHAIN_SELECTORS
    return reserved


def _check_extra_args(command: Sequence[str], extra_args: Sequence[str]) -> None:
    """Refuse an `extra_args` entry naming an option `command` already sets.

    bitcoind takes the last of a repeated option with no diagnostic
    (measured against the release `node-integration.yml` pins, given
    `-datadir` twice), so an `extra_args` entry that names one of
    `command`'s own options replaces it silently once appended after
    `command` -- refused here instead, before either ever reaches a
    process.

    :param command: the adapter's own argv, `_command()`'s answer.
    :param extra_args: what a caller asks to append after it.
    :raises ValueError: an entry of `extra_args` names an option
        `command` already sets, spelled with one or two leading dashes,
        with or without a `-no` negation.
    """
    reserved = _reserved_option_names(command)
    for arg in extra_args:
        name = _option_name(arg)
        if name is None:
            continue
        canonical = _canonical_option_name(name)
        if canonical in reserved:
            err_msg = f"extra_args reuses -{canonical}, which this adapter sets itself"
            raise ValueError(err_msg)


def _read_output(path: Path, offset: int = 0) -> str:
    """Return what a node wrote to `path` past `offset`, for an error on it.

    A file the node never created reads as empty: a log is opened by the
    node itself, and a node stuck before opening it has written nothing.

    :param path: where the node's own stderr was redirected, or its log.
    :param offset: where in `path` this start's own writes begin.
    """
    try:
        with path.open("rb") as output:
            output.seek(offset)
            return output.read().decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return ""


def _size(path: Path) -> int:
    """Return `path`'s size, `0` where it does not exist yet.

    :param path: the node's own log, read before the node is spawned.
    """
    return path.stat().st_size if path.exists() else 0


def _log_since(log_path: Path | None, log_offset: int) -> str:
    """Return a startup error's log section, or `""` where there is no log.

    :param log_path: the file the node logs to, `None` where none is known.
    :param log_offset: `log_path`'s size before the process was spawned.
    """
    if log_path is None:
        return ""
    return f"\n-- {log_path} since this start:\n{_read_output(log_path, log_offset)}"


def _wait_for_rpc(
    rpc: _RpcProbe,
    process: _Process,
    stderr_path: Path,
    *,
    timeout: float = _STARTUP_TIMEOUT,
    log_path: Path | None = None,
    log_offset: int = 0,
    log_on_exit: bool = False,
) -> None:
    """Poll `rpc` until it answers, or fail with what the process did instead.

    Shared by every adapter rather than duplicated: a process
    exiting before its RPC ever answers is failed on that exit code
    rather than left to time out, `bitcoin-core-rpc`'s own transport
    failure -- an empty answer, a refused connection -- being the same
    "not yet listening" every node's own startup answers with.

    An answer is not always progress, though: an `RpcError` whose `code`
    is not `RPCErrorCode.IN_WARMUP`, or an `HttpError` whose `status` is
    401 or 403, is the node refusing rather than the node warming up, and
    waiting out the rest of `timeout` never turns either into a success.
    Core's own `wait_for_rpc_connection`
    (`test_framework/test_node.py`) draws the same line on its
    `JSONRPCException`, suppressing `-28` (`RPC_IN_WARMUP`) and `-342`
    and re-raising every other code at once; this follows it for the one
    of the two `bitcoin_core_rpc.RPCErrorCode` names, and adds the
    equivalent HTTP-layer case for a credential no retry can fix. Every
    other failure -- refused, reset, or the cookie not yet written -- is
    kept as `last_error` and raised as the `TimeoutError`'s own cause if
    the deadline is reached, so a caller reading the traceback sees what
    the node actually answered rather than a bare timeout.

    The `TimeoutError`'s own message names every failure it waited out,
    by type and with how many times each was raised, and the last one,
    the way Core's `wait_for_rpc_connection` words its "Unable to connect
    to bitcoind" (`ignored errors: ... latest: ...`). It then carries what
    the process wrote to stderr and to its log since this start, where
    the log's path is known: Core's `test_runner.py` prints, only when
    `--combinedlogslen` names a length, the last that many lines of one log
    combined from the framework and every node, where this suite has no
    runner of its own to print anything. The whole of what
    this start appended rather than a tail of it: a bitcoind that never
    answers can be running with its RPC bound on `::1` alone, a failed
    bind on `127.0.0.1` not being fatal while another address bound
    (`HTTPBindAddresses`, `src/httpserver.cpp`), and the line saying so,
    "Binding RPC on address 127.0.0.1 port ... failed.", comes early,
    ahead of what a tail would keep.

    :param rpc: the client to poll, one whole call at a time.
    :param process: the process whose own exit ends the wait early.
    :param stderr_path: where the process's own stderr was redirected;
        read back into the error raised on an early exit or a timeout.
    :param timeout: how long to wait before giving up.
    :param log_path: the file the node logs to, read back into the error
        raised on a timeout, and on an early exit where `log_on_exit`;
        `None` where the adapter knows no such file.
    :param log_offset: `log_path`'s size before the process was spawned,
        where this start's own lines begin.
    :param log_on_exit: whether the error raised on an early exit also
        carries the log, for a node whose stdout nobody reads. Left out
        otherwise, since a caller compares the message with the stderr text
        the node gave.
    :raises TimeoutError: the RPC never answered within `timeout`,
        chained to the last transient failure `rpc.call` raised.
    :raises RuntimeError: the process exited before its RPC answered, or
        the RPC answered with an error waiting cannot resolve.
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    ignored: Counter[str] = Counter()
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            err_msg = (
                f"node process exited with {exit_code} before its RPC "
                f"answered -- stderr: {_read_output(stderr_path)}"
            )
            if log_on_exit:
                err_msg += _log_since(log_path, log_offset)
            raise RuntimeError(err_msg)
        try:
            rpc.call("getblockchaininfo")
        except RpcError as e:
            if e.code != RPCErrorCode.IN_WARMUP:
                err_msg = f"node answered its RPC with an error: {e}"
                raise RuntimeError(err_msg) from e
            last_error = e
        except HttpError as e:
            if e.status in (401, 403):
                err_msg = f"node refused its RPC: {e}"
                raise RuntimeError(err_msg) from e
            last_error = e
        except Exception as e:  # noqa: BLE001
            # every other failure before the node is listening is the
            # same failure: refused, reset, or the cookie/credentials
            # not written yet -- kept only to report on a timeout
            last_error = e
        else:
            return
        ignored[type(last_error).__name__] += 1
        time.sleep(0.1)
    latest = "" if last_error is None else f", latest: {last_error!r}"
    err_msg = (
        f"node did not answer its RPC within {timeout} s "
        f"(ignored errors: {dict(ignored)}{latest}) "
        f"-- stderr: {_read_output(stderr_path)}"
    )
    err_msg += _log_since(log_path, log_offset)
    raise TimeoutError(err_msg) from last_error


class _Running(NamedTuple):
    """A process `start` spawned, and the file its stderr is redirected to."""

    process: subprocess.Popen[bytes]
    stderr_path: Path


class NodeAdapter(ABC):
    """One running node: how it starts, stops, restarts, and answers RPC.

    A subclass names its own command line (`_command`), its own RPC
    client (`_rpc_client`) and its own `capabilities`; this class is the
    process and RPC plumbing every node needs regardless -- rule 1's
    "how it starts, stops and restarts" and "how RPC authenticates",
    answered once here and specialised per node.

    `datadir`, `rpc_port` and `p2p_port` are the caller's to allocate --
    `free_port` above, and a fixture's own `tmp_path` -- rather than this
    class reaching for a default any other instance on the same machine
    could collide on.

    `extra_args` is appended after `_command`'s own argv: a caller
    asking for a fact `_command` does not already name -- a non-default
    `-blocksdir`, a `-conf` naming a file this same caller wrote --
    passes it here rather than a subclass growing a parameter for every
    option a test happens to need. An entry naming an option `_command`
    already sets is refused at construction, and in the replacement
    `restart` takes, rather than silently overriding it the way
    bitcoind's own last-one-wins parsing would.

    `rpc_auth` is the credential a subclass's own `_rpc_client` builds
    its readiness and its ordinary RPC client from instead of its own
    default (a cookie, `BitcoindAdapter`'s and, where the build writes
    one, `BtclibNodeAdapter`'s): a node started with `-rpcuser`/
    `-rpcpassword` or `-norpccookiefile` writes no cookie at all
    ([ISS functional-tests#34](https://github.com/btclib-org/functional-tests/issues/34)),
    so a client waiting on one never sees it and `start` times out
    rather than the node ever answering. The caller who put such a flag
    in `extra_args` already knows the plaintext credential it configured
    -- Core's own `rpc_users.py` builds its own `-rpcauth` lines the same
    way, `rpcauth.py`'s own hash of a password it also keeps -- so it is
    the caller's to pass here too, rather than something this class
    could derive from the command line after the fact: a `-rpcauth`
    value is a salted hash, and the plaintext behind it exists only where
    it was chosen.

    `trace_rpc` is Core's own `--tracerpc`, restated per adapter rather
    than as a global: `_rpc_transport` wraps this adapter's transport in
    `traced_transport` where it is set, so whichever client a subclass's
    own `_rpc_client` builds over it -- `rpc_auth`'s credential one
    included -- prints, rather than this base class building the client
    itself.

    The RPC connections are this adapter's, kept across calls and closed
    by `stop` (`_ThreadSessions`), so a restarted node is never asked
    over a connection its previous process accepted.

    `chain` is the chain the node runs, in Core's own `-chain=` vocabulary
    (`main`, `test`, `testnet4`, `signet`, `regtest`), `regtest` where a
    caller names none. A subclass's own `chains` names the ones its node
    can start; any other is refused at construction. The chain decides the
    option `_command` spells it with, and the subdirectory of `datadir` a
    subclass reads its cookie and its log from; it is not an `extra_args`
    entry, `_check_extra_args` refusing every chain selector once
    `_command` names one.
    """

    capabilities: AbstractSet[Capability]
    chains: AbstractSet[str] = frozenset({_DEFAULT_CHAIN})
    # whether `start` sends the process's stdout to `os.devnull` rather
    # than to ours: `True` for a node that prints its whole log there as
    # well as to its log file. `start` then also reads the log into the
    # error of an early exit, since what the node printed to stdout is gone.
    _discards_stdout: bool = False

    def __init__(
        self,
        executable: str,
        datadir: Path,
        rpc_port: int,
        p2p_port: int,
        extra_args: Sequence[str] = (),
        rpc_auth: tuple[str, str] | None = None,
        *,
        trace_rpc: bool = False,
        chain: str = _DEFAULT_CHAIN,
    ) -> None:
        if chain not in self.chains:
            err_msg = (
                f"{type(self).__name__} cannot start a node on chain {chain!r}: "
                f"its chains are {sorted(self.chains)}"
            )
            raise ValueError(err_msg)
        self._chain = chain
        self._executable = executable
        self._datadir = datadir
        self._rpc_port = rpc_port
        self._p2p_port = p2p_port
        _check_extra_args(self._command(), extra_args)
        self._extra_args = tuple(extra_args)
        self._rpc_auth = rpc_auth
        self._trace_rpc = trace_rpc
        self._running: _Running | None = None
        self._sessions = _ThreadSessions()

    @abstractmethod
    def _command(self) -> list[str]:
        """Return the argv this node starts with."""

    @abstractmethod
    def _rpc_client(self) -> BitcoinCoreRpcClient:
        """Return a fresh RPC client for this node, however it authenticates.

        Built over `_rpc_transport`, so every client of this adapter shares
        its connections.
        """

    def _rpc_transport(self) -> HttpTransport:
        """Return the transport every RPC client of this adapter is built over.

        This adapter's own `_ThreadSessions`, wrapped in `traced_transport`
        where `trace_rpc` is set.
        """
        if self._trace_rpc:
            return traced_transport(self._sessions)
        return self._sessions

    def _log_path(self) -> Path | None:
        """Return the file this node logs to, or `None` where none is known.

        What `start` reads back into the error it raises on an RPC that
        never answered, and on an early exit where `_discards_stdout`; a
        subclass whose node writes a log at a path of this datadir's names it.
        """
        return None

    @property
    def chain(self) -> str:
        """Return the chain this node runs, in Core's `-chain=` vocabulary."""
        return self._chain

    @property
    def p2p_address(self) -> tuple[str, int]:
        """Return `(host, port)` the p2p wire can be dialled at."""
        return ("127.0.0.1", self._p2p_port)

    @property
    def rpc(self) -> BitcoinCoreRpcClient:
        """Return a fresh RPC client for this node, over its kept connections.

        Each access builds a client of its own, so what one caller sets on
        it reaches no other; the connection underneath is the adapter's,
        one per calling thread, and `stop` closes it.
        """
        return self._rpc_client()

    def start(self) -> None:
        """Spawn the process and wait for its RPC to answer.

        The process's own stderr is captured into a file under `datadir`
        rather than an unread `subprocess.PIPE`: a pipe nobody drains
        fills its kernel buffer once a long-running node writes past it,
        and then blocks the node on every write past that, where a file
        never blocks the writer regardless of how much it writes.

        Each start creates a file of its own under `_STDERR_DIR`, the way
        Core's own `TestNode.start` opens a `tempfile.NamedTemporaryFile`
        in its `stderr_dir`, and `stop` reads back the file of the start
        it ends: a second adapter over the same datadir writes into a
        file of its own rather than over the one a running node still
        writes to
        ([ISS 105](https://github.com/btclib-org/functional-tests/issues/105)).

        A start that raises leaves nothing running: the process is killed
        and forgotten before the error propagates, the way Core's own
        `TestNode.assert_start_raises_init_error` ends one, so a caller's
        own teardown has no process to lose track of and `stop` stays a
        no-op after it
        ([ISS 79](https://github.com/btclib-org/functional-tests/issues/79)).

        A process this adapter already holds is refused rather than
        replaced: the replaced one would keep running past `stop`, holding
        its datadir's lock and its ports
        ([ISS 92](https://github.com/btclib-org/functional-tests/issues/92)).
        Core's own `TestNode.assert_start_raises_init_error` asserts the
        same of its node before spawning one.

        :raises RuntimeError: this adapter already holds a process, which
            `stop` ends; or the process exited before answering, the
            message carrying what it wrote to stderr and, where
            `_discards_stdout` holds and `_log_path` names one, to its log
            since this start.
        :raises TimeoutError: the RPC never answered; the message carries
            the failures waited out, what the process wrote to stderr and,
            where `_log_path` names one, to its log since this start.
        """
        if self._running is not None:
            err_msg = "node already started: stop it before starting it again"
            raise RuntimeError(err_msg)
        self._start(self._extra_args)

    def _start(self, extra_args: tuple[str, ...]) -> None:
        """Do what `start` documents, appending `extra_args` to `_command`.

        :param extra_args: already checked against `_command`, by
            `__init__` or by `restart`.
        """
        stderr_dir = self._datadir / _STDERR_DIR
        stderr_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._log_path()
        log_offset = 0 if log_path is None else _size(log_path)
        with tempfile.NamedTemporaryFile(dir=stderr_dir, delete=False) as stderr_file:
            process = subprocess.Popen(  # noqa: S603
                [*self._command(), *extra_args],
                stdout=subprocess.DEVNULL if self._discards_stdout else None,
                stderr=stderr_file,
            )
        stderr_path = Path(stderr_file.name)
        self._running = _Running(process, stderr_path)
        try:
            _wait_for_rpc(
                self._rpc_client(),
                process,
                stderr_path,
                timeout=scaled(_STARTUP_TIMEOUT),
                log_path=log_path,
                log_offset=log_offset,
                log_on_exit=self._discards_stdout,
            )
        except BaseException:
            self._running = None
            try:
                process.kill()
                process.wait(timeout=scaled(_STARTUP_TIMEOUT))
            finally:
                self._sessions.close()
            raise

    def stop(self) -> None:
        """Terminate the process, wait for it to exit, and read how it did.

        A no-op where nothing was ever started, which is what lets a
        fixture's own teardown call this unconditionally rather than
        track whether `start` succeeded.

        The adapter's RPC connections are closed once the process has
        exited, whichever way this returns or raises: a call another
        thread still has in flight holds its connection until the node
        answers it or goes, so closing any earlier would wait on that
        call rather than on the node.

        A process still running once the wait expires is killed rather
        than left behind holding its datadir and ports, then waited for
        over the same bound, the way Core's own `TestNode.kill_process`
        ends one; the slow shutdown is then raised, not hidden
        ([ISS 76](https://github.com/btclib-org/functional-tests/issues/76)).

        An exit code other than `_CLEAN_EXIT` is raised too, whether the
        process had already exited before this call -- a crash during the
        test, which a test's own last RPC call would not have seen -- or
        exited so on `terminate`: Core's own `TestNode.stop_node` checks
        the code the same way
        ([ISS 84](https://github.com/btclib-org/functional-tests/issues/84)).
        A process that had already exited with `_CLEAN_EXIT`, the way an
        RPC `stop` ends one, is a clean stop.

        Stderr is carried in the error and does not fail a clean exit on
        its own, unlike Core's `expected_stderr=''`.

        Each of these raises only once the process has exited and been
        forgotten, so nothing is left running and a second `stop` is a
        no-op.

        :raises TimeoutError: the process ignored the termination for the
            whole wait and was killed; the message carries what it wrote
            to stderr.
        :raises RuntimeError: the process exited with a code other than
            `_CLEAN_EXIT`; the message carries the code, whether it had
            already exited before this call, and what it wrote to stderr.
        """
        if self._running is None:
            self._sessions.close()
            return
        (process, stderr_path), self._running = self._running, None
        try:
            exit_code, already_exited = self._terminate(process, stderr_path)
        finally:
            self._sessions.close()
        if exit_code != _CLEAN_EXIT:
            when = "before stop was called" if already_exited else "on terminate"
            err_msg = (
                f"node process exited with {exit_code} {when} -- "
                f"stderr: {_read_output(stderr_path)}"
            )
            raise RuntimeError(err_msg)

    def wait_until_stopped(
        self, *, timeout: float = _PROC_WAIT_TIMEOUT
    ) -> tuple[int, str]:
        """Wait for the process to exit on its own, and return how it did.

        Core's own `TestNode.wait_until_stopped`
        (`test_framework/test_node.py`), for a node that something other
        than `stop` ends: an RPC `stop`, or a fault a test provokes. Core
        checks the exit code and stderr itself; here the caller does.

        The process is forgotten once it has exited, and the adapter's RPC
        connections closed, so a later `stop` is a no-op. A process that
        does not exit in time is left running, and `stop` still ends it.

        :param timeout: how long to wait, before `--timeout-factor`'s own
            scaling (`timeout_factor.scaled`).
        :returns: the exit code, negative for a signal as `subprocess`
            reports it, and what the process wrote to stderr, stripped.
        :raises RuntimeError: this adapter holds no process.
        :raises TimeoutError: the process did not exit within `timeout`;
            the message carries what it wrote to stderr.
        """
        if self._running is None:
            err_msg = "no node process to wait for: start it first"
            raise RuntimeError(err_msg)
        process, stderr_path = self._running
        timeout = scaled(timeout)
        try:
            exit_code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            err_msg = (
                f"node process did not exit within {timeout} s -- "
                f"stderr: {_read_output(stderr_path)}"
            )
            raise TimeoutError(err_msg) from None
        self._running = None
        self._sessions.close()
        return exit_code, _read_output(stderr_path)

    @staticmethod
    def _terminate(
        process: subprocess.Popen[bytes], stderr_path: Path
    ) -> tuple[int, bool]:
        """Terminate `process` and wait for it, killing it if the wait expires.

        :param process: the process `stop` ends.
        :param stderr_path: where its stderr was redirected, read back into
            the error raised on a kill.
        :returns: the exit code, and whether the process had already exited
            before this call.
        :raises TimeoutError: the process ignored the termination for the
            whole wait and was killed.
        """
        already_exited = process.poll() is not None
        process.terminate()
        timeout = scaled(_STARTUP_TIMEOUT)
        try:
            return process.wait(timeout=timeout), already_exited
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout)
            err_msg = (
                f"node process ignored terminate for {timeout}s and was "
                f"killed -- stderr: {_read_output(stderr_path)}"
            )
            raise TimeoutError(err_msg) from None

    def restart(self, extra_args: Sequence[str] | None = None) -> None:
        """Stop and start again, over the same data directory.

        The one of the six parts every adapter answers identically: the
        data directory is the caller's, named once in `__init__`, so a
        restart resumes the same chain rather than a fresh one.

        `extra_args`, where given, replaces the constructor's own for this
        start alone, and a later `start` or `restart` without it goes back
        to the constructor's: Core's own `restart_node(i, extra_args)`
        (`test_framework.py`), whose `TestNode.start` falls back to the
        node's own `extra_args` wherever none is passed. An empty sequence
        is a start with no extra argument at all.

        :param extra_args: what to append after `_command`'s own argv for
            this start, in place of the constructor's.
        :raises ValueError: an entry of `extra_args` names an option
            `_command` already sets, refused the way `__init__` refuses
            one and before the running node is stopped.
        """
        if extra_args is None:
            args = self._extra_args
        else:
            _check_extra_args(self._command(), extra_args)
            args = tuple(extra_args)
        self.stop()
        self._start(args)

    def set_mock_time(self, timestamp: int) -> None:
        """Set this node's own clock, over `setmocktime`.

        Core's own `TestNode.setmocktime`
        (`test/functional/test_framework/test_node.py`) wraps the same
        RPC; `Capability.CLOCK` (`capability.py`) is what a caller checks
        before calling this, a node not declaring it having no
        `setmocktime` to wrap.

        :param timestamp: the unix time this node's own clock reads from
            now on; `0` releases it back to the wall clock.
        """
        self.rpc.call("setmocktime", [timestamp])

    def add_outbound_connection(
        self,
        address: tuple[str, int],
        connection_type: str,
        v2transport: bool = False,
    ) -> None:
        """Have this node dial `address` as an outbound `connection_type`.

        Over `addconnection`, the RPC Core's own
        `TestNode.add_outbound_p2p_connection`
        (`test/functional/test_framework/test_node.py`) makes the node
        dial a listening `P2PInterface` with; `peer.Listener` is what
        this suite listens with. `Capability.TYPED_OUTBOUND`
        (`capability.py`) is what a caller checks before calling this.

        `v2transport` defaults to `False`, the wire a `Peer` speaks by
        default, as `connect_nodes` above defaults to it.

        :param address: `(host, port)` to dial, a `Listener.address`.
        :param connection_type: `outbound-full-relay`, `block-relay-only`,
            `addr-fetch`, `feeler` or `manual`, the last where the build's
            own `help addconnection` names it.
        :param v2transport: `addconnection`'s own `v2transport` argument,
            whether the node dials over BIP324's v2 transport.
        """
        host, port = address
        self.rpc.call("addconnection", [f"{host}:{port}", connection_type, v2transport])

    def mine(self, count: int = 1) -> list[str]:
        """Mine `count` blocks client-side, return their hashes once connected.

        `Capability.MINE` (`capability.py`) is what a caller checks before
        calling this. A node that builds no block itself is mined this
        way; `BitcoindAdapter.mine` overrides it with `generatetoaddress`.

        `MiniWallet.generate` (`mini_wallet.py`) builds, solves and submits
        each block, extending whatever tip the node answers now, and pays
        its coinbase to that class's own anyone-can-spend script: a caller
        that means to spend what it mines holds a `MiniWallet` of its own
        instead. `submitblock` answering `None` says the block was stored,
        and a node may connect it later, so this polls `getbestblockhash`
        until it names the last block.

        :param count: how many blocks to mine.
        :returns: the mined blocks' own hashes, oldest first, the shape of
            `generatetoaddress`'s answer.
        :raises TimeoutError: the node stored the last block and never made
            it its tip.
        """
        hashes = [block_hash.hex() for block_hash in MiniWallet(self).generate(count)]
        if hashes:
            wait_until(lambda: self.rpc.call("getbestblockhash") == hashes[-1])
        return hashes

    def dial(self, address: str, *, v2transport: bool) -> None:
        """Have this node dial `address` once, as `connect_nodes` asks.

        Core's own `addnode <address> onetry <v2transport>`. A subclass
        whose node spells the call another way overrides this.

        :param address: `host:port` to dial.
        :param v2transport: `addnode`'s own third argument, whether the
            node dials over BIP324.
        """
        self.rpc.call("addnode", [address, "onetry", v2transport])


def _peer_ids(peers: object) -> set[object]:
    """Return every `id` a `getpeerinfo` answer carries, or an empty set.

    :param peers: whatever `getpeerinfo` answered.
    """
    if not isinstance(peers, list):
        return set()
    return {peer["id"] for peer in peers if isinstance(peer, dict)}


def _wait_for_peer(
    node: NodeAdapter,
    predicate: Callable[[dict[str, object]], bool],
    deadline: float,
    what: str,
) -> dict[str, object]:
    """Return the first `getpeerinfo` entry of `node` matching `predicate`.

    :param node: the node whose own `getpeerinfo` is polled.
    :param predicate: what the wanted entry looks like.
    :param deadline: a `time.monotonic()` value, not a duration.
    :param what: named in the exception, what this wait was for.
    :raises TimeoutError: no matching entry appeared before `deadline`.
    """
    while time.monotonic() < deadline:
        peers = node.rpc.call("getpeerinfo")
        if isinstance(peers, list):
            for peer in peers:
                if isinstance(peer, dict) and predicate(peer):
                    return peer
        time.sleep(0.1)
    err_msg = f"{node} never reported {what} within the wait"
    raise TimeoutError(err_msg)


def _wait_for_handshake(node: NodeAdapter, peer_id: object, deadline: float) -> None:
    """Wait until `node`'s own peer `peer_id` completed its handshake.

    `GetNodeStats` (`src/net.cpp`), what bitcoind's own `getpeerinfo`
    reads, lists every entry of `m_nodes` regardless of state, a peer
    short of `verack` included -- unlike btclib-node's own answer, whose
    docstring is "one entry per handshake-complete peer", nothing short
    of one ever reaching that list at all. `bytesrecv_per_msg` is
    Core's own field this checks a `pong` of at least 29 bytes against,
    the same wait Core's own `connect_nodes` makes for
    `fSuccessfullyConnected`, since `m_ping_start` starts at the clock's
    own epoch and so the first ping already goes out on the next
    message loop; its absence is read as btclib-node's own guarantee
    already met, rather than as a field to wait for. btcd gives the same
    guarantee: its `peerLifecycleHandler` (`server.go`) adds a peer to
    what `getpeerinfo` lists once that peer's `verack` arrives.

    :param node: the node whose own peer is polled.
    :param peer_id: the `id` `getpeerinfo` gave that peer.
    :param deadline: a `time.monotonic()` value, not a duration.
    :raises TimeoutError: the handshake never completed before `deadline`.
    """
    while time.monotonic() < deadline:
        peers = node.rpc.call("getpeerinfo")
        if isinstance(peers, list):
            for peer in peers:
                if not isinstance(peer, dict) or peer.get("id") != peer_id:
                    continue
                counters = peer.get("bytesrecv_per_msg")
                if not isinstance(counters, dict) or counters.get("pong", 0) >= 29:
                    return
                break
        time.sleep(0.1)
    err_msg = f"{node} never completed its own handshake with peer {peer_id!r}"
    raise TimeoutError(err_msg)


def wait_until(predicate: Callable[[], bool], *, timeout: float = 30.0) -> None:
    """Poll `predicate` every 0.1 s until it returns `True`, or raise.

    Core's own `TestNode.wait_until` (`test_framework/test_node.py`) calls
    `wait_until_helper_internal` (`test_framework/util.py`) with that
    node's `--timeout-factor`; this package spawns no `TestNode` for a
    method like that to live on, so a free function serves in its place,
    and a test-local wait that needs nothing beyond a predicate calls it
    instead of reimplementing this loop, unscaled, beside it
    ([ISS 90](https://github.com/btclib-org/functional-tests/issues/90)).

    :param predicate: checked every 0.1 s until it returns `True`.
    :param timeout: how long to keep polling, before `--timeout-factor`'s
        own scaling (`timeout_factor.scaled`).
    :raises TimeoutError: `predicate` never returned `True` within
        `timeout`.
    """
    timeout = scaled(timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.1)
    err_msg = f"condition not met within {timeout} s"
    raise TimeoutError(err_msg)


def connect_nodes(
    first: NodeAdapter,
    second: NodeAdapter,
    *,
    timeout: float = 30.0,
    v2transport: bool = False,
) -> None:
    """Connect `first` to `second`, over `addnode onetry` and `getpeerinfo`.

    Core's own `connect_nodes` (`test/functional/test_framework.py`):
    `addnode ... "onetry"`, `NodeAdapter.dial`, asks `first` to dial
    `second`'s own p2p address once, immediately. Core then waits for
    `getpeerinfo` to show the connection on *both* sides, matched by
    subversion, before
    waiting for each side's own `pong` to confirm the handshake is
    actually done; this matches the same three waits, by a criterion
    each side can actually be read off rather than by subversion, since
    this adapter's own nodes carry no per-node subversion tag to tell
    one apart from another. `first`'s own outbound entry is matched by
    address, `second`'s own p2p address being known and unique to it;
    `second`'s own new inbound entry cannot be matched the same way --
    its `addr` is `first`'s ephemeral outbound port, not the address
    `first` itself is reachable at -- so it is matched by an `id` absent
    from a `getpeerinfo` snapshot taken before the dial, the one entry a
    single `connect_nodes` call can have added since (rule 4's "the
    adapter translates a spelling" is why `_wait_for_handshake` reads
    `bytesrecv_per_msg` rather than assuming every adapter answers it:
    btclib-node's own `getpeerinfo` already withholds a peer until its
    handshake is done, so nothing here is adapter-specific except which
    of these waits is a no-op).

    Checking only `first`'s own view is racy under load: `second` can
    take longer to accept and register the same socket than `first`
    takes to see its own outbound half of it, which is what lets
    `sendmsgtopeer` on `second` answer "Could not send message to peer"
    moments after `first` alone would already report the connection.

    `addnode`'s own third argument, `v2transport`, is passed explicitly,
    `False` unless the caller asks otherwise, rather than left to `first`'s
    own default: bitcoind's own default is `True` from `v27.0`
    (`DEFAULT_V2_TRANSPORT`, `src/net.h`), and a `btclib-node` build without
    `-v2transport` reads and type-checks the argument without acting on it
    (`rpc/callbacks.py`'s own docstring). A bare `addnode ... "onetry"`
    opens with a v2 handshake, and bitcoind retries over v1 only where
    `V2Transport::ShouldReconnectV1` (`src/net.cpp`,
    bitcoin/bitcoin@f0de87f040) allows it. Measured against the release
    `node-integration.yml` pins dialling btclib-node 2026.11.7 started with
    `-nov2transport`, bitcoind's own `debug.log` reads "start sending v2
    handshake to peer=0", "socket closed, disconnecting peer=0" and
    "retrying with v1 transport protocol for peer=0". What stops the retry
    is the peer sending anything before it closes, which is why the
    argument is stated rather than left to a fallback:
    `ShouldReconnectV1` reads only the transport's state, and
    `DisconnectNodes` calls it however the socket closed (the closing
    comment of
    [ISS btclib-node#1197](https://github.com/btclib-org/btclib-node/issues/1197#issuecomment-5928114787)).
    Core's own `connect_nodes` (`test_framework.py`) makes the identical
    choice through its own `peer_advertises_v2` parameter, defaulting to
    whichever side is dialling; here the default is v1, and a test whose
    subject is BIP324 between two nodes declaring `Capability.V2TRANSPORT`
    passes `v2transport=True`.

    :param first: the node asked to dial.
    :param second: the node dialled.
    :param timeout: how long to wait for both sides to report the
        connection and its handshake, before `--timeout-factor`'s own
        scaling (`timeout_factor.scaled`).
    :param v2transport: `addnode`'s own `v2transport` argument, whether
        `first` dials over BIP324.
    :raises TimeoutError: either side never reported the connection, or
        its handshake, in time.
    """
    timeout = scaled(timeout)
    host, port = second.p2p_address
    address = f"{host}:{port}"
    before_second = _peer_ids(second.rpc.call("getpeerinfo"))
    first.dial(address, v2transport=v2transport)
    deadline = time.monotonic() + timeout

    first_peer = _wait_for_peer(
        first,
        lambda peer: peer.get("addr") == address and not peer.get("inbound"),
        deadline,
        f"a connection to {second}",
    )
    second_peer = _wait_for_peer(
        second,
        lambda peer: bool(peer.get("inbound")) and peer.get("id") not in before_second,
        deadline,
        f"a connection from {first}",
    )
    _wait_for_handshake(first, first_peer["id"], deadline)
    _wait_for_handshake(second, second_peer["id"], deadline)


def wait_until_disconnected(
    node: NodeAdapter, peer: NodeAdapter, *, timeout: float = 30.0
) -> None:
    """Wait until `node`'s own `getpeerinfo` no longer names `peer`'s address.

    Core's own `is_connected_to`
    (`test/functional/test_framework/test_node.py`) is `self.wait_until`
    wrapped around it, matched by `getnetworkinfo`'s own subversion
    string; this matches by p2p address instead, for the same reason
    `connect_nodes` above does -- this adapter's own nodes carry no
    per-node subversion tag. So `node` is the side that dialled: its
    outbound entry carries `peer`'s own listening address, where `peer`'s
    inbound entry for `node` carries an ephemeral port no `p2p_address`
    names, and a call with the two swapped returns at once.

    Unlike `disconnect_nodes` below, this makes no RPC call of its own:
    the drop it waits for is triggered some other way -- `rpc_setban`'s
    own subject, a `setban` on `peer`'s own side dropping the connection
    it matches -- and `disconnect_nodes` is `first.rpc.call
    ("disconnectnode", ...)` followed by exactly this wait, `first` for
    `node` and `second` for `peer`.

    :param node: the node whose own `getpeerinfo` is polled.
    :param peer: the peer whose address must disappear from it.
    :param timeout: how long to wait, before `--timeout-factor`'s own
        scaling.
    :raises TimeoutError: `node` still reports `peer` after `timeout`.
    """
    timeout = scaled(timeout)
    host, port = peer.p2p_address
    address = f"{host}:{port}"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        peers = node.rpc.call("getpeerinfo")
        if isinstance(peers, list) and address not in {p["addr"] for p in peers}:
            return
        time.sleep(0.1)
    err_msg = f"{node} still reports a peer at {address} after {timeout} s"
    raise TimeoutError(err_msg)


def disconnect_nodes(
    first: NodeAdapter, second: NodeAdapter, *, timeout: float = 30.0
) -> None:
    """Disconnect `first` from `second`, over `disconnectnode`.

    Core's own `disconnect_nodes`
    (`test/functional/test_framework/test_node.py`): `disconnectnode`
    asks `first` to drop `second`'s own p2p address, and
    `wait_until_disconnected` above is how this waits for the drop to
    land, the same wait `connect_nodes` above makes for a connection
    appearing rather than vanishing.

    Matched against `connect_nodes(first, second)`: `first` is the side
    that dialled, so `second`'s address is what its own outbound entry
    was recorded under, and `disconnectnode`'s address form is what asks
    for exactly that entry rather than one among several a node with
    other peers also carries.

    :param first: the node asked to drop the connection.
    :param second: the node dropped.
    :param timeout: how long to wait for `first` to stop reporting it,
        before `--timeout-factor`'s own scaling.
    :raises TimeoutError: `first` still reports the peer after `timeout`.
    """
    host, port = second.p2p_address
    address = f"{host}:{port}"
    first.rpc.call("disconnectnode", [address])
    wait_until_disconnected(first, second, timeout=timeout)


def wait_until_tips_agree(
    nodes: Sequence[NodeAdapter], *, timeout: float = 30.0
) -> None:
    """Poll every node's `getbestblockhash` until they all agree.

    Core's own `sync_blocks` (`test_framework.py`): propagation over p2p
    relay is asynchronous, so a block one node just mined or received
    reaches the rest of a topology on their own schedule, and this is the
    wait that turns "eventually" into a deadline this suite holds to
    rather than a race the assertion after it would otherwise be.

    :param nodes: the nodes to poll, at least one.
    :param timeout: how long to wait for every hash to match, before
        `--timeout-factor`'s own scaling.
    :raises TimeoutError: the nodes never agreed within `timeout`.
    """
    timeout = scaled(timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        hashes = {node.rpc.call("getbestblockhash") for node in nodes}
        if len(hashes) == 1:
            return
        time.sleep(0.1)
    err_msg = f"nodes did not converge on one tip within {timeout} s"
    raise TimeoutError(err_msg)


def wait_until_mempools_agree(
    nodes: Sequence[NodeAdapter], *, timeout: float = 30.0
) -> None:
    """Poll every node's `getrawmempool` until they all hold the same set.

    Core's own `sync_mempools` (`test_framework.py`): a transaction
    relayed over p2p reaches every node in a topology on its own
    schedule, exactly as a block does, which is `wait_until_tips_agree`
    above's own reason restated for the mempool rather than the chain.
    Core's own version also calls `syncwithvalidationinterfacequeue` on
    every node once they agree, flushing a background validation queue
    bitcoind's own tests care about; this drops it; it is a call this
    adapter's own node has no equivalent of, and no capability declares.

    :param nodes: the nodes to poll, at least one.
    :param timeout: how long to wait for every mempool to match, before
        `--timeout-factor`'s own scaling.
    :raises TimeoutError: the nodes never agreed within `timeout`.
    """
    timeout = scaled(timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        pools = {frozenset(node.rpc.call("getrawmempool")) for node in nodes}
        if len(pools) == 1:
            return
        time.sleep(0.1)
    err_msg = f"nodes did not converge on one mempool within {timeout} s"
    raise TimeoutError(err_msg)


def sync_all(nodes: Sequence[NodeAdapter], *, timeout: float = 30.0) -> None:
    """Wait for every node's tip, then every node's mempool, to agree.

    Core's own `sync_all` (`test_framework.py`): `wait_until_tips_agree`
    first, since a mempool's own transactions are commonly what a block
    just mined is meant to clear -- checking the mempool first could
    observe an old one, on a node whose new tip has not landed yet.

    :param nodes: the nodes to wait on, at least one.
    :param timeout: forwarded to each of the two waits in turn, each
        scaling it by `--timeout-factor`, so the call bounds at twice the
        scaled value rather than once.
    """
    wait_until_tips_agree(nodes, timeout=timeout)
    wait_until_mempools_agree(nodes, timeout=timeout)
