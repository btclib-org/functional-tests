# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""The test peers and the mock clock of Core's p2p functional tests.

`Peer.wait_for` drops what it does not wait for, and Core's peers answer
the node while a test waits, so a `Conn` reads its connection itself and
hands each message to `_handle` first. `Conn` answers `ping`; `RelayConn`
is Core's `PeerTxRelayer`, which also asks for whatever the node
announces and records what the node asks of it. A test file's own peer
subclasses `Conn` and extends `_handle`. `Conn.inbound` and
`Conn.outbound` connect one, and `Clock` is Core's mock time.

This module holds no test: its name ends `_test` for the repository's
`name-tests-test` hook.
"""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING, Self, override

from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import (
    GetData,
    Inv,
    Inventory,
    InventoryType,
    Ping,
    Pong,
    TxPayload,
)

from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Listener, Peer
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from collections.abc import Callable
    from contextlib import ExitStack

    from btclib.p2p import Message, Payload, Version

    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "Clock",
    "Conn",
    "RelayConn",
]

_MAGIC = magic_from_chain("regtest")

# Core's own default wait, `P2PInterface`'s and `wait_until`'s
_WAIT = 60.0

# the wait Core's own `wait_for_parent_requests` passes
_PARENT_REQUESTS_WAIT = 10.0

_TX_TYPES = (InventoryType.MSG_TX, InventoryType.MSG_WTX)

# the commands Core's own `assert_no_immediate_response` reads
_RESPONSES = ("getdata", "inv", "tx")


class Conn:
    """Core's own `P2PInterface`, as far as answering `ping`.

    :param peer: the connection, its handshake done.
    :param version: the node's own `version`, which `Peer.handshake` returns.
    """

    def __init__(self, peer: Peer, version: Version) -> None:
        self.peer = peer
        self.version = version

    @classmethod
    def inbound(
        cls, stack: ExitStack, node: NodeAdapter, *, wtxidrelay: bool = True
    ) -> Self:
        """Core's own `add_p2p_connection`: a peer dialing the node.

        :param wtxidrelay: whether the handshake announces `wtxidrelay`.
        """
        peer = stack.enter_context(Peer(node.p2p_address, _MAGIC))
        conn = cls(peer, peer.handshake(wtxidrelay=wtxidrelay))
        conn.sync_with_ping()
        return conn

    @classmethod
    def outbound(
        cls,
        stack: ExitStack,
        node: NodeAdapter,
        *,
        connection_type: str = "outbound-full-relay",
    ) -> Self:
        """Core's own `add_outbound_p2p_connection`: the node dials a peer.

        :param connection_type: the node's connection type for the peer.
        """
        with Listener(_MAGIC) as listener:
            node.add_outbound_connection(listener.address, connection_type)
            peer = stack.enter_context(listener.accept())
        conn = cls(peer, peer.handshake())
        conn.sync_with_ping()
        return conn

    def send(self, payload: Payload) -> None:
        """Core's own `send_without_ping`."""
        self.peer.send(payload)

    def _handle(self, message: Message) -> None:
        """Core's `on_ping`."""
        if message.command == "ping":
            self.send(Pong(Ping.parse(message.payload).nonce))

    def _receive(self, deadline: float) -> Message:
        """Return the next message once `_handle` has answered it.

        :param deadline: a `time.monotonic()` value, not a duration.
        :raises ConnectionError: the node closed the connection.
        :raises TimeoutError: no message arrived before `deadline`.
        """
        err_msg = "the wait ran out"
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(err_msg)
        try:
            message = self.peer.receive(timeout=remaining)
        except TimeoutError:
            raise TimeoutError(err_msg) from None
        self._handle(message)
        return message

    def sync_with_ping(self) -> None:
        """`Peer.sync_with_ping`'s barrier, every message read answered.

        :raises ConnectionError: the node closed the connection.
        :raises TimeoutError: no matching `pong` arrived within the wait.
        """
        nonce = secrets.randbelow(2**64 - 1) + 1
        self.send(Ping(0))
        self.send(Ping(nonce))
        deadline = time.monotonic() + scaled(_WAIT)
        while True:
            message = self._receive(deadline)
            if message.command == "pong" and Pong.parse(message.payload).nonce == nonce:
                return

    def send_and_ping(self, payload: Payload) -> None:
        """Core's own `send_and_ping`: `send`, then `sync_with_ping`."""
        self.send(payload)
        self.sync_with_ping()

    def wait_until(
        self, predicate: Callable[[], bool], *, timeout: float = _WAIT
    ) -> None:
        """Core's own `P2PInterface.wait_until`, the peer read before a poll."""

        def _served() -> bool:
            self.sync_with_ping()
            return predicate()

        wait_until(_served, timeout=timeout)

    def wait_for_disconnect(self) -> None:
        """Read and answer until the node closes the connection.

        :raises TimeoutError: the connection was still open at the wait's
            end.
        """
        deadline = time.monotonic() + scaled(_WAIT)
        try:
            while True:
                self._receive(deadline)
        except ConnectionError:
            return


class RelayConn(Conn):
    """Core's own `PeerTxRelayer`: a peer recording what the node asks of it.

    `last_getdata` is the items of the latest `getdata`, Core's
    `last_message["getdata"]`; `requested` the hash of every item any
    `getdata` named, Core's `getdata_received`; `tx_invs` the hash of every
    transaction an `inv` announced, Core's `P2PTxInvStore.get_invs`.
    """

    def __init__(self, peer: Peer, version: Version) -> None:
        super().__init__(peer, version)
        self.last_getdata: tuple[Inventory, ...] = ()
        self.requested: set[bytes] = set()
        self.tx_invs: list[bytes] = []

    @override
    def _handle(self, message: Message) -> None:
        """Core's `on_getdata`, `on_inv`, `P2PTxInvStore`'s, and `on_ping`."""
        super()._handle(message)
        if message.command == "getdata":
            self.last_getdata = GetData.parse(message.payload).items
            self.requested.update(item.hash for item in self.last_getdata)
        elif message.command == "inv":
            items = Inv.parse(message.payload).items
            self.tx_invs += [item.hash for item in items if item.type_code in _TX_TYPES]
            if wanted := [item for item in items if item.type_code]:
                self.send(GetData(wanted))

    def wait_for_getdata(self, hashes: list[bytes]) -> None:
        """Core's own `wait_for_getdata`: the last `getdata` names `hashes`."""
        self.wait_until(lambda: [item.hash for item in self.last_getdata] == hashes)

    def wait_for_parent_requests(self, txids: list[bytes]) -> None:
        """Core's own `wait_for_parent_requests`.

        The latest `getdata` asks for exactly `txids`, each as a
        `MSG_WITNESS_TX`.
        """

        def _requested() -> bool:
            items = self.last_getdata
            return len(items) == len(txids) and all(
                item.type_code == InventoryType.MSG_WITNESS_TX and item.hash in txids
                for item in items
            )

        self.wait_until(_requested, timeout=_PARENT_REQUESTS_WAIT)

    def wait_for_request(self, txhash: bytes) -> None:
        """Wait for the latest `getdata` to name `txhash`, among any others.

        Core's `test_maximal_package_protected` waits so on its peer, where
        `wait_for_getdata` asks for the latest to name exactly its hashes.
        """
        self.wait_until(lambda: txhash in [item.hash for item in self.last_getdata])

    def wait_for_tx(self, txid: bytes) -> None:
        """Core's own `wait_for_tx`: read until a `tx` of `txid` arrives.

        :raises ConnectionError: the node closed the connection.
        :raises TimeoutError: no such `tx` arrived within the wait.
        """
        deadline = time.monotonic() + scaled(_WAIT)
        while True:
            message = self._receive(deadline)
            if (
                message.command == "tx"
                and TxPayload.parse(message.payload).tx.id == txid
            ):
                return

    def was_asked(self) -> bool:
        """Whether any `getdata` came, Core's `"getdata" in last_message`."""
        return "getdata" in self.peer.last_message

    def assert_no_immediate_response(self, payload: Payload) -> None:
        """Core's own `assert_no_immediate_response`.

        No `getdata`, `inv` or `tx` answers `payload` ahead of the `pong`
        of a ping round trip: the latest of each is the one there was
        before.
        """
        before = {
            command: self.peer.last_message.get(command) for command in _RESPONSES
        }
        self.send_and_ping(payload)
        for command in _RESPONSES:
            assert self.peer.last_message.get(command) == before[command]

    def assert_never_requested(self, txhash: bytes) -> None:
        """Core's own `assert_never_requested`, after a ping round trip."""
        self.sync_with_ping()
        assert txhash not in self.requested


class Clock:
    """Core's own mock time: `setmocktime` once, then `bumpmocktime`.

    :param node: the node whose clock this sets, to the wall clock now.
    """

    def __init__(self, node: NodeAdapter) -> None:
        self._node = node
        self.now = int(time.time())
        node.set_mock_time(self.now)

    def bump(self, seconds: int) -> None:
        """Core's own `bumpmocktime`: move the node's clock `seconds` on."""
        self.now += seconds
        self._node.set_mock_time(self.now)
