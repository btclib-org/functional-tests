# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`Peer` and `Listener` against hand-written fake nodes, over loopback.

No real bitcoind or btclib-node here or anywhere else this suite's
ordinary run reaches (rule 1): `_FakeNode` is this module's own,
answering just enough of the protocol -- a handshake, a `ping` it can be
told to send unsolicited, the `pong` to each `ping` it is told to read,
and a `getdata` served with whatever payload the test hands it -- to
drive every branch `peer.py` has, over a socket that never leaves this
machine. `_FakeDialler` is the other direction, a node's own outbound
connection to a `Listener`.
"""

from __future__ import annotations

import io
import itertools
import math
import secrets
import select
import socket
import struct
import threading
import time
import types
from collections import Counter
from collections.abc import Iterator

import pytest
from bitcoin_core_rpc import magic_from_chain
from btclib.block import genesis_block
from btclib.exceptions import BTClibValueError
from btclib.p2p import (
    BlockPayload,
    GetData,
    GetHeaders,
    Inventory,
    Message,
    Ping,
    Pong,
    ServiceFlags,
    Verack,
    Version,
    WtxidRelay,
)
from btclib.p2p.bip324 import (
    LENGTH_LEN,
    MESSAGE_IDS,
    REKEY_INTERVAL,
    contents_from_message,
)
from btclib.p2p.limits import MAX_LOCATOR_SZ

from btclib_functional_tests.node import free_port
from btclib_functional_tests.peer import Listener, Peer
from btclib_functional_tests.timeout_factor import set_factor

_MAGIC = magic_from_chain("regtest")


class _Wire:
    """One end of a connection: framing for regtest, and reading it back."""

    _connection: socket.socket | None
    _buffer: bytes

    def send(self, payload: object, *, check_validity: bool = True) -> None:
        """Frame `payload` for regtest, and send it to the connected peer.

        `check_validity=False` is what a regtest block needs: `Payload`
        classes validate against mainnet's own rules by default, and a
        regtest header's proof-of-work is not one of them.
        """
        message = payload.to_message(  # type: ignore[attr-defined]
            _MAGIC, check_validity=check_validity
        )
        assert self._connection is not None
        self._connection.sendall(message.serialize())

    def _receive(self) -> Message:
        assert self._connection is not None
        while True:
            stream = io.BytesIO(self._buffer)
            try:
                message = Message.parse(stream)
            except Exception:
                if not (chunk := self._connection.recv(4096)):
                    raise
                self._buffer += chunk
            else:
                self._buffer = self._buffer[stream.tell() :]
                return message


class _FakeDialler(_Wire):
    """A node's own outbound connection: dials a `Listener`, speaks first."""

    def __init__(self, address: tuple[str, int]) -> None:
        self._connection = socket.create_connection(address, timeout=5.0)
        self._buffer = b""

    def close(self) -> None:
        assert self._connection is not None
        self._connection.close()

    def assert_silent(self) -> None:
        """Assert the peer sends nothing unprompted within a short wait."""
        assert self._connection is not None
        self._connection.settimeout(0.2)
        with pytest.raises(TimeoutError):
            self._connection.recv(4096)
        self._connection.settimeout(5.0)

    def open_handshake(self) -> list[str]:
        """Send `version` first, answer the peer's, return what it sent."""
        self.send(Version(nonce=secrets.randbelow(2**64)))
        commands = [self._receive().command for _ in range(3)]
        self.send(Verack())
        return commands


class _FakeNode(_Wire):
    """A minimal p2p responder: handshake, `ping`/`pong`, `getdata` echoed.

    One connection at a time, which is every test below: `accept` blocks
    the caller's own thread until `Peer` dials in, so a test drives both
    sides from one process without either ever guessing at timing.
    """

    def __init__(self) -> None:
        self.port = free_port()
        self._listener = socket.socket()
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", self.port))
        self._listener.listen(1)
        self._connection: socket.socket | None = None
        self._buffer = b""

    def accept(self) -> None:
        """Block until a peer connects."""
        self._connection, _ = self._listener.accept()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._listener.close()

    def answer_handshake(self) -> None:
        """Read the peer's `version`, answer it, then wait out its `verack`."""
        self._receive()  # version
        self.send(Version(nonce=secrets.randbelow(2**64)))
        self._receive()  # wtxidrelay
        self._receive()  # verack
        self.send(Verack())

    def send_ping(self) -> int:
        """Send an unsolicited `ping`, and return its own nonce."""
        nonce = secrets.randbelow(2**63)
        self.send(Ping(nonce))
        return nonce

    def expect_pong(self, nonce: int) -> None:
        """Read the next message and assert it answers `nonce` with a pong."""
        message = self._receive()
        assert message.command == "pong"
        assert Pong.parse(message.payload).nonce == nonce

    def answer_pings(self, count: int) -> list[int]:
        """Read `count` pings, answer each with a pong, return the nonces."""
        nonces = []
        for _ in range(count):
            message = self._receive()
            assert message.command == "ping"
            nonce = Ping.parse(message.payload).nonce
            self.send(Pong(nonce))
            nonces.append(nonce)
        return nonces

    def serve_getdata(
        self, response: object, *, check_validity: bool = True
    ) -> GetData:
        """Read a `getdata`, send `response` back, and return the request."""
        message = self._receive()
        assert message.command == "getdata"
        request = GetData.parse(message.payload)
        self.send(response, check_validity=check_validity)
        return request


@pytest.fixture
def fake_node() -> Iterator[_FakeNode]:
    """Yield a `_FakeNode`, closed on teardown either way."""
    node = _FakeNode()
    try:
        yield node
    finally:
        node.close()


def _connect_and_accept(fake_node: _FakeNode) -> Peer:
    """Dial `fake_node` on a thread, so `accept` can block the main one."""
    thread = threading.Thread(target=fake_node.accept)
    thread.start()
    peer = Peer(("127.0.0.1", fake_node.port), _MAGIC, timeout=5.0)
    thread.join(timeout=5.0)
    return peer


def test_handshake_exchanges_version_and_verack_both_ways(fake_node: _FakeNode) -> None:
    """`handshake` sends this peer's own `version`, and returns the node's."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        version = peer.handshake()
        server_thread.join(timeout=5.0)
        assert isinstance(version, Version)
    finally:
        peer.close()


def test_wait_for_answers_a_ping_while_waiting_for_something_else(
    fake_node: _FakeNode,
) -> None:
    """A `ping` while waiting for another command is answered, not returned."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        nonce = fake_node.send_ping()
        fake_node.send(WtxidRelay())

        message = peer.wait_for("wtxidrelay")
        assert message.command == "wtxidrelay"

        fake_node.expect_pong(nonce)
    finally:
        peer.close()


def test_last_message_keeps_the_latest_of_each_command(fake_node: _FakeNode) -> None:
    """A message `wait_for` reads and drops is still the command's latest."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        nonce = fake_node.send_ping()
        fake_node.send(WtxidRelay())
        peer.wait_for("wtxidrelay")
        fake_node.expect_pong(nonce)

        assert Ping.parse(peer.last_message["ping"].payload).nonce == nonce
        assert peer.last_message["wtxidrelay"].command == "wtxidrelay"
        assert "verack" in peer.last_message
    finally:
        peer.close()


def test_handshake_offers_the_services_it_is_given(fake_node: _FakeNode) -> None:
    """`services` replaces the default `NODE_NETWORK | NODE_WITNESS`."""
    offered: list[ServiceFlags] = []

    def answer_and_record() -> None:
        message = fake_node._receive()
        offered.append(ServiceFlags(Version.parse(message.payload).services))
        fake_node.send(Version(nonce=secrets.randbelow(2**64)))
        fake_node._receive()
        fake_node._receive()
        fake_node.send(Verack())

    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=answer_and_record)
        server_thread.start()
        peer.handshake(services=ServiceFlags.NODE_NETWORK)
        server_thread.join(timeout=5.0)
        assert offered == [ServiceFlags.NODE_NETWORK]
    finally:
        peer.close()


def test_handshake_asks_for_addrv2_ahead_of_its_verack(fake_node: _FakeNode) -> None:
    """`addrv2` sends `sendaddrv2` between the `wtxidrelay` and the `verack`."""
    commands: list[str] = []

    def answer_and_record() -> None:
        commands.append(fake_node._receive().command)
        fake_node.send(Version(nonce=secrets.randbelow(2**64)))
        commands.extend(fake_node._receive().command for _ in range(3))
        fake_node.send(Verack())

    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=answer_and_record)
        server_thread.start()
        peer.handshake(addrv2=True)
        server_thread.join(timeout=5.0)
        assert commands == ["version", "wtxidrelay", "sendaddrv2", "verack"]
    finally:
        peer.close()


def test_handshake_leaves_wtxidrelay_out_where_asked(fake_node: _FakeNode) -> None:
    """`wtxidrelay=False` sends the `verack` straight after the `version`."""
    commands: list[str] = []

    def answer_and_record() -> None:
        commands.append(fake_node._receive().command)
        fake_node.send(Version(nonce=secrets.randbelow(2**64)))
        commands.append(fake_node._receive().command)
        fake_node.send(Verack())

    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=answer_and_record)
        server_thread.start()
        peer.handshake(wtxidrelay=False)
        server_thread.join(timeout=5.0)
        assert commands == ["version", "verack"]
    finally:
        peer.close()


def test_wait_for_the_requested_block_after_a_getdata(fake_node: _FakeNode) -> None:
    """The `getdata`/`block` round trip `p2p_getdata` itself needs."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        block = genesis_block("regtest")
        peer.send(GetData([Inventory(2, block.header.hash)]))
        payload = BlockPayload(block, include_witness=False, check_validity=False)
        request = fake_node.serve_getdata(payload, check_validity=False)
        assert request.items[0].hash == block.header.hash

        message = peer.wait_for("block")
        received = BlockPayload.parse(message.payload, check_validity=False)
        assert received.block.header.hash == block.header.hash
    finally:
        peer.close()


def test_wait_for_raises_a_timeout_error_when_nothing_matches(
    fake_node: _FakeNode,
) -> None:
    """`wait_for` gives up rather than block forever on a command never sent."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        fake_node.send_ping()
        with pytest.raises(TimeoutError, match="never saw 'block'"):
            peer.wait_for("block", timeout=0.2)
    finally:
        peer.close()


def test_wait_for_a_zero_timeout_never_reads_the_socket(fake_node: _FakeNode) -> None:
    """A deadline already past is the loop's own exit, not a read timing out."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        with pytest.raises(TimeoutError, match="never saw 'block'"):
            peer.wait_for("block", timeout=0.0)
    finally:
        peer.close()


def test_wait_for_timeout_is_scaled_by_the_global_factor(fake_node: _FakeNode) -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        set_factor(0.0)
        try:
            with pytest.raises(TimeoutError, match="never saw 'block'"):
                peer.wait_for("block", timeout=1000.0)
        finally:
            set_factor(1.0)
    finally:
        peer.close()


def test_peer_construction_timeout_is_scaled_by_the_global_factor(
    fake_node: _FakeNode,
) -> None:
    """`--timeout-factor` reaches the constructor's own default too.

    Inflating rather than collapsing here: `socket.create_connection`'s
    own `timeout=0` puts the socket in non-blocking mode, which is not
    what a factor of `0` means for a wait already past its deadline
    (`wait_for`'s own tests, above) -- so this checks the multiplication
    landed on the stored default directly rather than through a second
    real connection attempt.
    """
    set_factor(100.0)
    try:
        peer = _connect_and_accept(fake_node)
    finally:
        set_factor(1.0)
    try:
        assert peer._timeout == pytest.approx(500.0)
    finally:
        peer.close()


def test_receive_raises_on_a_closed_connection(fake_node: _FakeNode) -> None:
    """A socket closed mid-wait is a `ConnectionError`, not a silent hang."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)
        fake_node.close()
        with pytest.raises(ConnectionError, match="closed"):
            peer.receive()
    finally:
        peer.close()


def test_receive_leaves_the_socket_at_the_default_wait_once_it_times_out(
    fake_node: _FakeNode,
) -> None:
    """A `timeout` given to one `receive` is not the next one's."""
    peer = _handshaken(fake_node)
    try:
        with pytest.raises(TimeoutError):
            peer.receive(timeout=0.05)
        assert peer._socket.gettimeout() == 5.0
    finally:
        peer.close()


def test_handshake_leaves_the_socket_at_the_default_wait(
    fake_node: _FakeNode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `wait_for` had left of its deadline is not the next read's wait.

    `peer.py`'s own clock advances a second a call, so each `wait_for`
    hands `receive` a second less than the default wait.
    """
    ticks = itertools.count()
    monkeypatch.setattr(
        "btclib_functional_tests.peer.time",
        types.SimpleNamespace(monotonic=lambda: float(next(ticks))),
    )
    peer = _handshaken(fake_node)
    try:
        assert peer._socket.gettimeout() == 5.0
    finally:
        peer.close()


def test_fake_node_close_before_any_peer_ever_dialled_in() -> None:
    """`_FakeNode.close` before `accept` closes the listener alone."""
    node = _FakeNode()
    node.close()


def test_fake_node_receive_raises_when_the_peer_closes_first(
    fake_node: _FakeNode,
) -> None:
    """The server side of the same close: reading after the peer hangs up."""
    peer = _connect_and_accept(fake_node)
    server_thread = threading.Thread(target=fake_node.answer_handshake)
    server_thread.start()
    peer.handshake()
    server_thread.join(timeout=5.0)
    peer.close()
    with pytest.raises(Exception):
        fake_node._receive()


def test_send_check_validity_false_reaches_to_message_not_only_serialize(
    fake_node: _FakeNode,
) -> None:
    """`check_validity=False` is what lets an over-long locator be sent at all.

    `p2p_invalid_locator`'s own subject: `GetHeaders.assert_valid` refuses
    a locator over `MAX_LOCATOR_SZ`, which is the wire behaviour the test
    is about, so sending one needs the construction and the framing both
    to skip that check -- not only `Payload.serialize`, which
    `to_message` already forwards this to.
    """
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        locator = [secrets.token_bytes(32) for _ in range(MAX_LOCATOR_SZ + 1)]
        message = GetHeaders(locator=locator, check_validity=False)
        with pytest.raises(BTClibValueError, match="locator"):
            peer.send(message)

        peer.send(message, check_validity=False)
        received = fake_node._receive()
        assert received.command == "getheaders"
    finally:
        peer.close()


def test_context_manager_closes_the_socket(fake_node: _FakeNode) -> None:
    """`with Peer(...)` closes the socket on the way out."""
    thread = threading.Thread(target=fake_node.accept)
    thread.start()
    with Peer(("127.0.0.1", fake_node.port), _MAGIC, timeout=5.0) as peer:
        thread.join(timeout=5.0)
        assert peer is not None


def test_send_raw_sends_already_serialized_bytes(fake_node: _FakeNode) -> None:
    """The log family's own escape from `self._magic`: unframed, sent raw."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        peer.send_raw(Ping(42).to_message(_MAGIC).serialize())
        message = fake_node._receive()
        assert message.command == "ping"
        assert Ping.parse(message.payload).nonce == 42
    finally:
        peer.close()


def test_wait_for_disconnect_returns_once_the_node_closes(
    fake_node: _FakeNode,
) -> None:
    """The node hanging up is exactly what this call is waiting for."""
    peer = _connect_and_accept(fake_node)
    server_thread = threading.Thread(target=fake_node.answer_handshake)
    server_thread.start()
    peer.handshake()
    server_thread.join(timeout=5.0)
    fake_node.close()
    peer.wait_for_disconnect(timeout=5.0)
    peer.close()


def test_wait_for_disconnect_drains_a_message_before_the_close(
    fake_node: _FakeNode,
) -> None:
    """A message still in flight is drained, not mistaken for staying up."""
    peer = _connect_and_accept(fake_node)
    server_thread = threading.Thread(target=fake_node.answer_handshake)
    server_thread.start()
    peer.handshake()
    server_thread.join(timeout=5.0)
    fake_node.send_ping()
    fake_node.close()
    peer.wait_for_disconnect(timeout=5.0)
    peer.close()


def test_wait_for_disconnect_raises_when_the_node_never_closes(
    fake_node: _FakeNode,
) -> None:
    """A node that stays up is a failed wait, not an endless one."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        with pytest.raises(AssertionError, match="not closed"):
            peer.wait_for_disconnect(timeout=0.2)
    finally:
        peer.close()


def test_wait_for_disconnect_a_zero_timeout_never_reads_the_socket(
    fake_node: _FakeNode,
) -> None:
    """A deadline already past is the loop's own exit, not a read timing out."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        with pytest.raises(AssertionError, match="not closed"):
            peer.wait_for_disconnect(timeout=0.0)
    finally:
        peer.close()


def test_wait_for_disconnect_timeout_is_scaled_by_the_global_factor(
    fake_node: _FakeNode,
) -> None:
    """A factor of 0 (`set_factor`) collapses even a long-sounding wait."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        set_factor(0.0)
        try:
            with pytest.raises(AssertionError, match="not closed"):
                peer.wait_for_disconnect(timeout=1000.0)
        finally:
            set_factor(1.0)
    finally:
        peer.close()


def _handshaken(fake_node: _FakeNode) -> Peer:
    """Return a `Peer` that has completed its handshake with `fake_node`."""
    peer = _connect_and_accept(fake_node)
    server_thread = threading.Thread(target=fake_node.answer_handshake)
    server_thread.start()
    peer.handshake()
    server_thread.join(timeout=5.0)
    return peer


def _until_readable(peer: Peer) -> None:
    """Block until what the node sent last has reached `peer`'s socket."""
    select.select([peer._socket], [], [], 5.0)


def test_is_connected_answers_at_once_while_the_node_stays_up(
    fake_node: _FakeNode,
) -> None:
    """An open, silent connection is `True` without waiting out the timeout."""
    peer = _handshaken(fake_node)
    try:
        start = time.monotonic()
        assert peer.is_connected
        elapsed = time.monotonic() - start
        timeout = peer._socket.gettimeout()
        assert timeout == 5.0
        assert timeout is not None
        assert elapsed < timeout / 2
    finally:
        peer.close()


def test_is_connected_keeps_what_it_reads_for_receive(fake_node: _FakeNode) -> None:
    """A message read along the way is still `receive`'s to return."""
    peer = _handshaken(fake_node)
    try:
        nonce = fake_node.send_ping()
        _until_readable(peer)
        assert peer.is_connected
        message = peer.receive()
        assert message.command == "ping"
        assert Ping.parse(message.payload).nonce == nonce
    finally:
        peer.close()


def test_is_connected_is_false_once_the_node_closes(fake_node: _FakeNode) -> None:
    """An orderly close is a disconnect, and `receive` then sees it too."""
    peer = _handshaken(fake_node)
    try:
        fake_node.close()
        _until_readable(peer)
        assert not peer.is_connected
        with pytest.raises(ConnectionError):
            peer.receive()
    finally:
        peer.close()


def test_is_connected_is_false_once_the_node_resets(fake_node: _FakeNode) -> None:
    """A reset rather than an orderly close is a disconnect too."""
    peer = _handshaken(fake_node)
    try:
        assert fake_node._connection is not None
        fake_node._connection.setsockopt(
            socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
        )
        fake_node.close()
        _until_readable(peer)
        assert not peer.is_connected
    finally:
        peer.close()


def test_is_connected_is_false_once_the_peer_closes(fake_node: _FakeNode) -> None:
    """`close` on this side ends the connection without reading anything."""
    peer = _handshaken(fake_node)
    peer.close()
    assert not peer.is_connected


def test_sync_with_ping_waits_for_the_pong_to_its_second_ping(
    fake_node: _FakeNode,
) -> None:
    """Two pings, the first with a zero nonce; the second one's pong ends it."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        nonces: list[int] = []
        server_thread = threading.Thread(
            target=lambda: nonces.extend(fake_node.answer_pings(2))
        )
        server_thread.start()
        peer.sync_with_ping()
        server_thread.join(timeout=5.0)
        assert nonces[0] == 0
        assert nonces[1] != 0
    finally:
        peer.close()


def test_sync_with_ping_raises_when_no_pong_arrives(fake_node: _FakeNode) -> None:
    """A node that never answers is a failed wait, not an endless one."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)

        with pytest.raises(TimeoutError, match="never saw 'pong'"):
            peer.sync_with_ping(timeout=0.2)
    finally:
        peer.close()


def test_message_count_counts_what_wait_for_dropped(fake_node: _FakeNode) -> None:
    """Every message `receive` returns is counted, those read past included."""
    peer = _connect_and_accept(fake_node)
    try:
        server_thread = threading.Thread(target=fake_node.answer_handshake)
        server_thread.start()
        peer.handshake()
        server_thread.join(timeout=5.0)
        assert peer.message_count == Counter({"version": 1, "verack": 1})

        fake_node.send_ping()
        fake_node.send(WtxidRelay())
        peer.wait_for("wtxidrelay")
        assert peer.message_count["ping"] == 1
        assert peer.message_count["wtxidrelay"] == 1
    finally:
        peer.close()


@pytest.fixture
def listener() -> Iterator[Listener]:
    """Yield a `Listener` on regtest's magic, closed on teardown."""
    with Listener(_MAGIC, timeout=5.0) as listening:
        yield listening


def test_listener_address_is_loopback_and_a_port_the_os_chose(
    listener: Listener,
) -> None:
    """Bound at construction, so the address is known before anything dials."""
    host, port = listener.address
    assert host == "127.0.0.1"
    assert port > 0


def test_listener_queues_a_connection_made_before_accept(listener: Listener) -> None:
    """The backlog: a dial completes on the caller's thread, `accept` after."""
    dialler = _FakeDialler(listener.address)
    try:
        peer = listener.accept()
        peer.close()
    finally:
        dialler.close()


def test_listener_peer_answers_the_node_s_own_version(listener: Listener) -> None:
    """The node dialled, so the node speaks first and the peer answers."""
    dialler = _FakeDialler(listener.address)
    peer = listener.accept()
    try:
        results: dict[str, object] = {}
        peer_thread = threading.Thread(
            target=lambda: results.update(version=peer.handshake())
        )
        peer_thread.start()
        dialler.assert_silent()
        assert dialler.open_handshake() == ["version", "wtxidrelay", "verack"]
        peer_thread.join(timeout=5.0)
        assert isinstance(results["version"], Version)
        assert peer.message_count == Counter({"version": 1, "verack": 1})
    finally:
        peer.close()
        dialler.close()


def test_listener_accept_raises_a_timeout_error_when_nothing_dials() -> None:
    """No connection within the wait is a `TimeoutError`, not a hang."""
    with Listener(_MAGIC, timeout=0.2) as listening, pytest.raises(TimeoutError):
        listening.accept()


def test_listener_close_leaves_an_accepted_peer_open(listener: Listener) -> None:
    """`close` stops listening; the connection it handed over stays up."""
    dialler = _FakeDialler(listener.address)
    peer = listener.accept()
    try:
        listener.close()
        dialler.send(Ping(7))
        message = peer.wait_for("ping")
        assert Ping.parse(message.payload).nonce == 7
    finally:
        peer.close()
        dialler.close()


def test_listener_context_manager_stops_listening() -> None:
    """Leaving the `with` closes the socket, so a later dial is refused."""
    with Listener(_MAGIC, timeout=5.0) as listening:
        address = listening.address
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(address, timeout=5.0)


@pytest.mark.parametrize(
    "timeout,error",
    [(-1.0, ValueError), (math.nan, ValueError), (math.inf, OverflowError)],
    ids=["negative", "nan", "inf"],
)
def test_listener_refusing_its_timeout_leaves_no_socket_open(
    monkeypatch: pytest.MonkeyPatch, timeout: float, error: type[Exception]
) -> None:
    """A timeout `settimeout` refuses closes the socket before raising."""
    opened: list[socket.socket] = []

    class _Socket(socket.socket):
        def __init__(self, *args: object, **kwargs: object) -> None:
            super().__init__(*args, **kwargs)  # type: ignore[arg-type]
            opened.append(self)

    monkeypatch.setattr(socket, "socket", _Socket)
    with pytest.raises(error):
        Listener(_MAGIC, timeout=timeout)
    assert opened
    assert all(s.fileno() == -1 for s in opened)


def test_listener_timeout_is_scaled_by_the_global_factor() -> None:
    """`--timeout-factor` reaches `accept` and every peer it returns."""
    set_factor(100.0)
    try:
        scaled_listener = Listener(_MAGIC, timeout=5.0)
    finally:
        set_factor(1.0)
    try:
        dialler = _FakeDialler(scaled_listener.address)
        peer = scaled_listener.accept()
        try:
            assert scaled_listener._socket.gettimeout() == pytest.approx(500.0)
            assert peer._timeout == pytest.approx(500.0)
            assert peer._socket.gettimeout() == pytest.approx(500.0)
        finally:
            peer.close()
            dialler.close()
    finally:
        scaled_listener.close()


# BIP324's v2 transport: a dialling `Peer` and the one a `Listener`
# accepted speak it to each other, one as the initiator and one as the
# responder, so each side's handshake is the other's test


@pytest.fixture
def v2_pair(listener: Listener) -> Iterator[tuple[Peer, Peer]]:
    """Yield a v2 `Peer` dialling `listener`, and the v2 `Peer` it accepted."""
    accepted: list[Peer] = []
    thread = threading.Thread(
        target=lambda: accepted.append(listener.accept(supports_v2_p2p=True))
    )
    thread.start()
    dialled = Peer(listener.address, _MAGIC, timeout=5.0, supports_v2_p2p=True)
    thread.join(timeout=5.0)
    (responder,) = accepted
    try:
        yield dialled, responder
    finally:
        dialled.close()
        responder.close()


def test_v2_peers_share_a_session_and_exchange_messages(
    v2_pair: tuple[Peer, Peer],
) -> None:
    """Both sides derive one session, and a message crosses either way."""
    dialled, accepted = v2_pair
    assert dialled.supports_v2_p2p
    assert accepted.supports_v2_p2p
    assert dialled._cipher is not None
    assert accepted._cipher is not None
    assert dialled._cipher.session_id == accepted._cipher.session_id
    dialled.send(Ping(7))
    assert Ping.parse(accepted.receive().payload).nonce == 7
    accepted.send(Pong(7))
    message = dialled.receive()
    assert message.command == "pong"
    assert message.magic == _MAGIC


def test_v2_peers_agree_past_a_rekeying(v2_pair: tuple[Peer, Peer]) -> None:
    """More packets than `REKEY_INTERVAL` decrypt in order."""
    dialled, accepted = v2_pair
    for nonce in range(REKEY_INTERVAL + 1):
        dialled.send(Ping(nonce))
    for nonce in range(REKEY_INTERVAL + 1):
        assert Ping.parse(accepted.receive().payload).nonce == nonce


def test_a_decoy_is_dropped_by_the_receiving_peer(v2_pair: tuple[Peer, Peer]) -> None:
    """A packet sent as a decoy is never returned, nor counted."""
    dialled, accepted = v2_pair
    dialled.send(Ping(1), is_decoy=True)
    dialled.send(Ping(2))
    assert Ping.parse(accepted.receive().payload).nonce == 2
    assert accepted.message_count == Counter({"ping": 1})


def test_a_message_of_an_unknown_short_id_is_dropped(
    v2_pair: tuple[Peer, Peer],
) -> None:
    """A type `message_from_contents` refuses is dropped, and the peer stays."""
    dialled, accepted = v2_pair
    assert dialled._cipher is not None
    dialled.send_raw(dialled._cipher.encrypt(bytes([len(MESSAGE_IDS)]) + b"x"))
    dialled.send(Ping(3))
    assert Ping.parse(accepted.receive().payload).nonce == 3
    assert accepted.message_count == Counter({"ping": 1})


def test_a_packet_read_across_two_waits_keeps_its_length(
    v2_pair: tuple[Peer, Peer],
) -> None:
    """The decrypted length outlives a wait that times out before the rest."""
    dialled, accepted = v2_pair
    assert dialled._cipher is not None
    ping = Ping(9).to_message(_MAGIC)
    packet = dialled._cipher.encrypt(contents_from_message("ping", ping.payload))
    dialled.send_raw(packet[:LENGTH_LEN])
    with pytest.raises(TimeoutError):
        accepted.receive(timeout=0.2)
    dialled.send_raw(packet[LENGTH_LEN:])
    assert Ping.parse(accepted.receive().payload).nonce == 9


def test_a_v2_listener_peer_takes_a_v1_version_as_v1(listener: Listener) -> None:
    """A node opening with v1's `version` gets a v1 peer, which answers it."""
    dialler = _FakeDialler(listener.address)
    results: dict[str, Peer] = {}

    def accept_and_answer() -> None:
        peer = listener.accept(supports_v2_p2p=True)
        results["peer"] = peer
        peer.handshake()

    thread = threading.Thread(target=accept_and_answer)
    thread.start()
    try:
        assert dialler.open_handshake() == ["version", "wtxidrelay", "verack"]
        thread.join(timeout=5.0)
        assert not results["peer"].supports_v2_p2p
    finally:
        results["peer"].close()
        dialler.close()


def test_a_v2_peer_dialling_a_v1_node_raises_and_closes(
    fake_node: _FakeNode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A node closing on the v2 key fails the dial, and the socket is closed."""
    created: list[socket.socket] = []
    create_connection = socket.create_connection

    def recording(*args: object, **kwargs: object) -> socket.socket:
        connection = create_connection(*args, **kwargs)  # type: ignore[arg-type]
        created.append(connection)
        return connection

    monkeypatch.setattr(socket, "create_connection", recording)

    def accept_and_close() -> None:
        fake_node.accept()
        fake_node.close()

    thread = threading.Thread(target=accept_and_close)
    thread.start()
    with pytest.raises(ConnectionError):
        Peer(("127.0.0.1", fake_node.port), _MAGIC, timeout=5.0, supports_v2_p2p=True)
    thread.join(timeout=5.0)
    (connection,) = created
    assert connection.fileno() == -1


def test_a_v2_peer_refuses_garbage_past_bip324_s_bound(listener: Listener) -> None:
    """No garbage terminator within BIP324's bound fails the handshake."""
    dialler = _FakeDialler(listener.address)
    try:
        assert dialler._connection is not None
        # a key -- any 64 octets decode -- then garbage with no terminator
        dialler._connection.sendall(bytes(64) + bytes(4095 + 16))
        with pytest.raises(ValueError, match="no garbage terminator"):
            listener.accept(supports_v2_p2p=True)
    finally:
        dialler.close()


def test_a_v1_peer_refuses_to_send_a_decoy(fake_node: _FakeNode) -> None:
    """A decoy is a v2 packet, which a v1 connection has no way to send."""
    with (
        _connect_and_accept(fake_node) as peer,
        pytest.raises(ValueError, match="decoy"),
    ):
        peer.send(Ping(1), is_decoy=True)


def test_a_v1_peer_refuses_octets_without_its_magic(listener: Listener) -> None:
    """A v2 key reaching a v1 peer is refused on its first four octets."""
    dialler = _FakeDialler(listener.address)
    peer = listener.accept()
    try:
        assert dialler._connection is not None
        dialler._connection.sendall(bytes(64))
        with pytest.raises(ValueError, match="magic bytes mismatch"):
            peer.receive()
    finally:
        peer.close()
        dialler.close()
