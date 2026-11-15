# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`NodeAdapter`'s kept RPC connections, against a local HTTP/1.1 server.

The server is `http.server`'s own, speaking keep-alive on `127.0.0.1` and
answering JSON-RPC 2.0, and it counts the connections it accepts and the
ones that end: what a node would see of `_ThreadSessions`, with no node
started. The process `start` spawns is a `MagicMock`, as in `node_test.py`.
"""

from __future__ import annotations

import json
import select
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, cast, override
from unittest.mock import MagicMock, patch
from urllib.request import Request

import pytest
from bitcoin_core_rpc import BitcoinCoreRpcClient, HttpError

from btclib_functional_tests.node import NodeAdapter, _ThreadSessions

if TYPE_CHECKING:
    from collections.abc import Iterator
    from collections.abc import Set as AbstractSet
    from pathlib import Path
    from socket import socket

    from btclib_functional_tests.capability import Capability

# how long a test waits on the server, and the client's own timeout
_WAIT = 5.0

# how long the server holds `block` without a `release`: past the client's
# timeout, so that a call answered in time was answered because of `release`
_HOLD = 2 * _WAIT

# what `_HttpAdapter`'s client authenticates with, which `_Handler` never reads
_CREDENTIAL = ("user", "pw")


class _Server(ThreadingHTTPServer):
    """A JSON-RPC server counting the connections it accepts and ends."""

    daemon_threads = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.hang_up = False
        # `block` is held until `release` is set, or for `_HOLD`, the way a
        # node holds a `waitfornewblock` until something happens -- its own
        # shutdown included -- and `blocked` says the request has arrived
        self.blocked = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Condition()
        self.accepted = 0
        self.ended = 0

    @property
    def port(self) -> int:
        """Return the port this server listens on."""
        return int(self.server_address[1])

    def count_accepted(self) -> None:
        """Record one connection accepted."""
        with self._lock:
            self.accepted += 1

    def count_ended(self) -> None:
        """Record one connection ended, waking `wait_ended`."""
        with self._lock:
            self.ended += 1
            self._lock.notify_all()

    @override
    def shutdown_request(self, request: socket | tuple[bytes, socket]) -> None:
        # count after the close, not in `finish`, which runs before it
        super().shutdown_request(request)
        self.count_ended()

    def wait_ended(self, count: int) -> None:
        """Block until `count` connections have ended, or fail."""
        with self._lock:
            assert self._lock.wait_for(lambda: self.ended >= count, _WAIT)


class _Handler(BaseHTTPRequestHandler):
    """Answer each POST with its method's name, or a 500 for `fail`."""

    protocol_version = "HTTP/1.1"

    @property
    def _server(self) -> _Server:
        """Return the `_Server` this handler answers for."""
        return cast("_Server", self.server)

    @override
    def setup(self) -> None:
        super().setup()
        self._server.count_accepted()

    def do_POST(self) -> None:
        """Answer one JSON-RPC 2.0 request, keeping the connection open."""
        length = int(self.headers["Content-Length"])
        request = json.loads(self.rfile.read(length))
        method = request["method"]
        if method == "block":
            self._server.blocked.set()
            self._server.release.wait(_HOLD)
        reply: dict[str, object] = {"jsonrpc": "2.0", "id": request["id"]}
        if method == "fail":
            status = 500
            reply["error"] = {"code": -1, "message": "refused"}
        else:
            status = 200
            reply["result"] = method
        body = json.dumps(reply).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        # hung up on without a `Connection: close`, the way a node's own
        # idle timeout ends a kept connection between two calls
        self.close_connection = self._server.hang_up

    @override
    def log_message(self, format: str, *args: object) -> None:
        del format, args


@pytest.fixture
def server() -> Iterator[_Server]:
    """Serve on a thread of its own for the test's length."""
    httpd = _Server()
    thread = threading.Thread(target=httpd.serve_forever, args=(0.01,))
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


class _HttpAdapter(NodeAdapter):
    """A `NodeAdapter` whose client is real, over `_rpc_transport`."""

    capabilities: AbstractSet[Capability] = frozenset()

    @override
    def _command(self) -> list[str]:
        return ["fake-node", f"-datadir={self._datadir}"]

    @override
    def _rpc_client(self) -> BitcoinCoreRpcClient:
        user, password = _CREDENTIAL
        return BitcoinCoreRpcClient(
            f"http://127.0.0.1:{self._rpc_port}",
            user=user,
            password=password,
            timeout=_WAIT,
            transport=self._rpc_transport(),
        )


@pytest.fixture
def adapter(server: _Server, tmp_path: Path) -> Iterator[_HttpAdapter]:
    """Build an adapter over `server`'s port, stopped however the test ends."""
    node = _HttpAdapter("fake-node", tmp_path / "node", server.port, 0)
    try:
        yield node
    finally:
        node.stop()


def _running() -> MagicMock:
    """Return a process `start` sees running and `stop` sees exit cleanly."""
    process = MagicMock()
    process.poll.return_value = None
    process.wait.return_value = 0
    return process


def test_calls_share_the_connection_start_waited_on(
    server: _Server, adapter: _HttpAdapter
) -> None:
    """`start`'s own poll and every later client ride one connection."""
    with patch("subprocess.Popen", return_value=_running()):
        adapter.start()
    assert adapter.rpc.call("getblockcount") == "getblockcount"
    assert adapter.rpc.call("getbestblockhash") == "getbestblockhash"
    assert server.accepted == 1


def test_stop_closes_the_connection(server: _Server, adapter: _HttpAdapter) -> None:
    """`stop` ends the kept connection, which the server then sees end."""
    with patch("subprocess.Popen", return_value=_running()):
        adapter.start()
    assert server.ended == 0
    adapter.stop()
    server.wait_ended(1)
    assert (server.accepted, server.ended) == (1, 1)


def test_restart_asks_over_a_fresh_connection(
    server: _Server, adapter: _HttpAdapter
) -> None:
    """`restart` ends the first process's connection and asks over a new one."""
    with patch("subprocess.Popen", return_value=_running()):
        adapter.start()
        adapter.restart()
    server.wait_ended(1)
    assert adapter.rpc.call("getblockcount") == "getblockcount"
    assert server.accepted == 2


def _wait_for_hang_up(adapter: NodeAdapter) -> None:
    """Block until the client's kept socket sees the server's close, or fail.

    The server having closed does not mean the client has seen it yet. The
    socket is reached through `NodeAdapter._sessions`,
    `_ThreadSessions._local` and `SessionTransport._connections`.
    """
    session = adapter._sessions._local.session
    (connection,) = session._connections.values()
    readable, _, _ = select.select([connection.sock], [], [], _WAIT)
    assert readable


def test_a_connection_the_node_hung_up_on_is_replaced(
    server: _Server, adapter: _HttpAdapter
) -> None:
    """A call after the node closed the kept connection opens another."""
    server.hang_up = True
    assert adapter.rpc.call("getblockcount") == "getblockcount"
    server.wait_ended(1)
    _wait_for_hang_up(adapter)
    assert adapter.rpc.call("getblockcount") == "getblockcount"
    assert server.accepted == 2


def test_an_rpc_error_leaves_the_connection_in_use(
    server: _Server, adapter: _HttpAdapter
) -> None:
    """A reply with HTTP 500 is still a reply: the connection stays in use."""
    with pytest.raises(HttpError) as refused:
        adapter.rpc.call("fail")
    assert refused.value.status == 500
    assert adapter.rpc.call("getblockcount") == "getblockcount"
    assert server.accepted == 1


def test_each_thread_keeps_a_connection_of_its_own(
    server: _Server, adapter: _HttpAdapter
) -> None:
    """One client, called from two threads, rides two connections."""
    client = adapter.rpc
    assert client.call("getblockcount") == "getblockcount"
    called = threading.Event()
    done = threading.Event()

    def call_and_wait() -> None:
        client.call("getblockcount")
        called.set()
        done.wait(_WAIT)

    worker = threading.Thread(target=call_and_wait)
    worker.start()
    try:
        assert called.wait(_WAIT)
        assert (server.accepted, server.ended) == (2, 0)
    finally:
        done.set()
        worker.join()
    adapter.stop()
    server.wait_ended(2)


def test_a_thread_that_ends_closes_its_connection(
    server: _Server, adapter: _HttpAdapter
) -> None:
    """A thread's kept connection ends with the thread, not with `stop`."""
    client = adapter.rpc
    assert client.call("getblockcount") == "getblockcount"
    worker = threading.Thread(target=client.call, args=("getblockcount",))
    worker.start()
    worker.join()
    server.wait_ended(1)
    assert (server.accepted, server.ended) == (2, 1)
    assert client.call("getblockcount") == "getblockcount"
    assert server.accepted == 2


def test_trace_rpc_prints_over_the_kept_connection(
    server: _Server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`trace_rpc` wraps the adapter's transport rather than replacing it."""
    adapter = _HttpAdapter(
        "fake-node", tmp_path / "node", server.port, 0, trace_rpc=True
    )
    try:
        adapter.rpc.call("getblockcount")
        adapter.rpc.call("getblockcount")
    finally:
        adapter.stop()
    assert server.accepted == 1
    assert capsys.readouterr().out.count(" getblockcount\n<-- 200 ") == 2


def test_thread_sessions_hands_each_call_its_timeout() -> None:
    """The client's timeout reaches the thread's own `SessionTransport`."""
    session = MagicMock(return_value=(200, b"{}"))
    sessions = _ThreadSessions(factory=lambda: session)
    request = Request("http://127.0.0.1:1", data=b"{}")
    assert sessions(request, 12.5) == (200, b"{}")
    session.assert_called_once_with(request, 12.5)
    sessions.close()
    session.close.assert_called_once_with()


def test_stop_terminates_without_waiting_on_a_call_in_flight(
    server: _Server, adapter: _HttpAdapter
) -> None:
    """A call held by the node ends with its answer once `stop` terminates it.

    The node answers the held call only once `terminate` reaches it, so a
    `stop` closing the connections before terminating would wait on that
    call until the client's own timeout, and the call would end in it: the
    server holds the call past that timeout, so it cannot answer first.
    """
    process = _running()
    process.terminate.side_effect = server.release.set
    with patch("subprocess.Popen", return_value=process):
        adapter.start()
    answers: list[object] = []
    client = adapter.rpc
    worker = threading.Thread(target=lambda: answers.append(client.call("block")))
    worker.start()
    assert server.blocked.wait(_WAIT)
    adapter.stop()
    worker.join(_WAIT)
    assert answers == ["block"]
