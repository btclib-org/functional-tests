# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `interface_http`, its excessive request size step, over each node.

Read from Core's `test/functional/interface_http.py` (`228877d7836a`,
2026-10-10)'s `check_excessive_request_size`: the node's HTTP server
answers a request just under its header limit and its body limit, and
closes the connection on one just over either. The file's other steps are
not ported here.

Core asserts the status of each refusal in bitcoind's log, since the client
may see the connection close before it reads the answer. The step is split
as the log family splits it
([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)). The
wire half reads the answers under the limits, the closed connections over
them, and a refusal's 400 where it arrives before the close. The log half
asks for `Capability.DEBUG_LOG` and reads bitcoind's `HTTPResponse` lines,
written under the `-debug=http` `BitcoindAdapter` starts with.

Core's own HTTP server is in bitcoind from bitcoin/bitcoin#35182, which
a release before `v32.0` does not carry. Such a release answers 404 to
a request whose short header lines pass the limit only together, and
writes no `HTTPResponse` line, as `node-integration.yml`'s `older-core`
job measures against each release it runs. The body reads the build's
own `getnetworkinfo` `version`
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
On an older build the wire half asserts that 404, and the log half
asserts no `HTTPResponse` line is written. `_HTTP_SERVER_VERSION`'s own
comment has the known limit.

Each `interface_http_<node>_test.py` module beside this one runs every
body, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import contextlib
import http.client
from typing import TYPE_CHECKING, Self
from urllib.parse import urlsplit

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "excessive_request_size_is_logged",
    "excessive_request_size_is_refused",
]

# Core's own `MAX_HEADERS_SIZE` and `MAX_BODY_SIZE`, the limits its test
# reads from `httpserver.h`
_MAX_HEADERS_SIZE = 8192
_MAX_BODY_SIZE = 32 * 1024 * 1024

# Core's own allowance for the header lines `http.client` adds itself
_DEFAULT_HEADERS_SIZE = 1000

_HEADER_LINE_SIZE = len("header_0000: foo\r\n")

# Core's own `NETWORK_ERRORS` (`test_framework/netutil.py`): what a client
# may see where the server closes the connection before it is answered
_NETWORK_ERRORS = (
    BrokenPipeError,
    ConnectionResetError,
    ConnectionAbortedError,
    http.client.ResponseNotReady,
)

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which bitcoind serves HTTP with
# its own server: `32.0`'s, the first release carrying bitcoin/bitcoin#35182
# (`9c20859b5f`, merged 2026-06-22 while `master` read `31.99`). A known
# limit: a `master` build from that merge up to the move to `32.99`
# (`f3fec67c3e`, 2026-09-11) reports `319900` and carries the server, so
# this test fails against such a build
#
# `serves_through_libevent` (`rpc_echo_payload_test.py`) reads the `logging`
# categories, which bitcoin/bitcoin#35597 changed eight days after
# bitcoin/bitcoin#35182. This test reads the version instead, so it is wrong
# on a wider range of `master` builds, as the paragraph above says
_HTTP_SERVER_VERSION = 320000


def _own_http_server(adapter: NodeAdapter) -> bool:
    """Whether `adapter`'s node does what Core's check asserts of `master`.

    Core's own claim for every node, bitcoind before `_HTTP_SERVER_VERSION`
    excepted, read off its own `getnetworkinfo` `version`
    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
    """
    if not isinstance(adapter, BitcoindAdapter):
        return True
    version: int = adapter.rpc.call("getnetworkinfo")["version"]
    return version >= _HTTP_SERVER_VERSION


class _Connection:
    """Core's `BitcoinHTTPConnection`, the part this step uses."""

    def __init__(
        self,
        adapter: NodeAdapter,
        extra_headers: Mapping[str, str] | None = None,
    ) -> None:
        url = urlsplit(adapter.rpc.url)
        if url.hostname is None or url.port is None:
            err_msg = f"{adapter.rpc.url} names no host and port"
            raise ValueError(err_msg)
        self.conn = http.client.HTTPConnection(
            url.hostname, url.port, timeout=adapter.rpc.timeout
        )
        self.conn.connect()
        self.headers = {"Authorization": adapter.rpc.auth_header()}
        self.headers |= extra_headers or {}

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.conn.close()

    def get(self, path: str) -> http.client.HTTPResponse:
        """Send a `GET` of `path`, and return the answer."""
        self.conn.request("GET", path, "", self.headers)
        return self.conn.getresponse()

    def post(self, path: str, body: str) -> http.client.HTTPResponse:
        """Send a `POST` of `body` to `path`, and return the answer."""
        self.conn.request("POST", path, body, self.headers)
        return self.conn.getresponse()

    def post_raw(self, path: str, body: str) -> None:
        """Write a `POST` of `body` to the socket, reading no answer.

        The socket is given a timeout of its own first, so a server that
        stops reading without closing fails the send, as Core's own
        `send_request.result(timeout=5)` does.
        """
        data = body.encode()
        head = (
            f"POST {path} HTTP/1.1\r\n"
            f"Authorization: {self.headers['Authorization']}\r\n"
            f"Content-Length: {len(data)}\r\n\r\n"
        )
        sock = self.conn.sock
        if sock is None:
            err_msg = "the connection holds no socket"
            raise ConnectionError(err_msg)
        sock.settimeout(scaled(5.0))
        sock.sendall(head.encode("ascii") + data)

    def closed(self) -> bool:
        """Whether the server closed the connection, Core's `sock_closed`."""
        if self.conn.sock is None:
            return True
        try:
            self.conn.request("GET", "/")
            self.conn.getresponse().read()
        except _NETWORK_ERRORS:
            return True
        return False


def _uri(size: int) -> str:
    """Return a path of `size` octets past its leading slash."""
    return f"/{'x' * size}"


def _header_lines(count: int) -> dict[str, str]:
    """Return `count` short header lines, Core's `header_{i:04}: foo`."""
    return {f"header_{i:04}": "foo" for i in range(count)}


def _submitblock(size: int) -> str:
    """Return a `submitblock` request whose body is `size` octets long."""
    prefix = '{"jsonrpc": "2.0", "id": "0", "method": "submitblock", "params": ["'
    suffix = '"]}'
    return prefix + "0" * (size - len(prefix) - len(suffix)) + suffix


def _assert_refused(
    conn: _Connection, send: Callable[[], http.client.HTTPResponse]
) -> None:
    """Core's `send_bad_and_tolerate_disconnect`: send, and check the close.

    A 400 is checked where it arrives before the close.
    """
    with contextlib.suppress(*_NETWORK_ERRORS):
        response = send()
        assert response.status == http.client.BAD_REQUEST
        response.read()
    assert conn.closed()


def _send_tolerating_close(send: Callable[[], object]) -> None:
    """Send, whatever the server answers, or close the connection instead."""
    with contextlib.suppress(*_NETWORK_ERRORS):
        response = send()
        if isinstance(response, http.client.HTTPResponse):
            response.read()


def excessive_request_size_is_refused(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: under each limit answered, over it closed.

    :param adapter: a node's own adapter fixture.
    """
    own_server = _own_http_server(adapter)
    below_uri = _uri(_MAX_HEADERS_SIZE - _DEFAULT_HEADERS_SIZE)
    with _Connection(adapter) as conn:
        assert conn.get(below_uri).status == http.client.NOT_FOUND
    with _Connection(adapter) as conn:
        _assert_refused(conn, lambda: conn.get(_uri(_MAX_HEADERS_SIZE)))

    below = (_MAX_HEADERS_SIZE - _DEFAULT_HEADERS_SIZE) // _HEADER_LINE_SIZE
    with _Connection(adapter, _header_lines(below)) as conn:
        assert conn.get("/x").status == http.client.NOT_FOUND
    above = _MAX_HEADERS_SIZE // _HEADER_LINE_SIZE
    with _Connection(adapter, _header_lines(above)) as conn:
        if own_server:
            _assert_refused(conn, lambda: conn.get("/x"))
        else:
            assert conn.get("/x").status == http.client.NOT_FOUND

    with _Connection(adapter) as conn:
        response = conn.post("/", _submitblock(_MAX_BODY_SIZE - 1))
        assert response.status == http.client.OK
    with _Connection(adapter) as conn:
        with contextlib.suppress(*_NETWORK_ERRORS):
            conn.post_raw("/", _submitblock(_MAX_BODY_SIZE + 1))
        wait_until(conn.closed, timeout=5.0)


def excessive_request_size_is_logged(
    adapter: NodeAdapter, skip_counts: SkipCounts
) -> None:
    """Check the log half: bitcoind's own status for each refusal.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    :raises TypeError: `adapter` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, adapter.capabilities, skip_counts)
    if not isinstance(adapter, BitcoindAdapter):
        err_msg = f"{type(adapter).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    own_server = _own_http_server(adapter)

    def logged(status: int) -> contextlib.AbstractContextManager[None]:
        line = [f"HTTPResponse (status code: {status}"]
        if own_server:
            return assert_debug_log(adapter.debug_log_path, line)
        return assert_debug_log(adapter.debug_log_path, [], line)

    with logged(http.client.BAD_REQUEST), _Connection(adapter) as conn:
        _send_tolerating_close(lambda: conn.get(_uri(_MAX_HEADERS_SIZE)))
    above = _MAX_HEADERS_SIZE // _HEADER_LINE_SIZE
    with (
        logged(http.client.BAD_REQUEST),
        _Connection(adapter, _header_lines(above)) as conn,
    ):
        _send_tolerating_close(lambda: conn.get("/x"))
    with logged(http.client.REQUEST_ENTITY_TOO_LARGE), _Connection(adapter) as conn:
        _send_tolerating_close(
            lambda: conn.post_raw("/", _submitblock(_MAX_BODY_SIZE + 1))
        )
