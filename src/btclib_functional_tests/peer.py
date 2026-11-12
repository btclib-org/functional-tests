# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`Peer`: the p2p connection a test drives, over `btclib.p2p`'s codecs.

Rule 5 of [ISS 2220](https://github.com/btclib-org/btclib/issues/2220):
btclib's own `tests/integration/` fixture is not shared, and this is tf2's
own peer rather than a port of Core's `P2PInterface`
(`test/functional/test_framework/p2p.py`) -- a synchronous socket and a
receive loop over `btclib.p2p.Message`, `Version` and the rest, where
Core's own class is `asyncio.Protocol` driven from a background thread.
What is read from Core's own class, rather than copied, is the shape of
the handshake and the convenience a test wants: `handshake` sends and
waits for both sides' `version`/`verack`, and `wait_for` is `wait_until`
narrowed to "the next message of this command", auto-answering a `ping`
along the way so a caller waiting on anything else is not the one that
has to remember to.

`Listener` is the other direction, Core's `peer_accept_connection`: a
socket the node is made to dial, the node's own outbound connection
arriving there as a `Peer` like any other.

A connection speaks the v1 wire unless it is asked for BIP324's v2
transport, Core's `supports_v2_p2p`. The handshake is Core's
`EncryptedP2PState` (`test/functional/test_framework/v2_p2p.py`), the
dialling side as the initiator and the accepting side as the responder.
Its ciphers, its key schedule and its packets are
`btclib.p2p.bip324`'s, its key exchange `btclib.ellswift`'s.
"""

from __future__ import annotations

import contextlib
import io
import secrets
import socket
import time
from collections import Counter
from typing import TYPE_CHECKING, Self

from btclib.ellswift import ELL_SIZE
from btclib.exceptions import BTClibValueError, IncompleteMessageError
from btclib.p2p import (
    Message,
    Payload,
    Ping,
    Pong,
    SendAddrV2,
    ServiceFlags,
    Verack,
    Version,
    WtxidRelay,
)
from btclib.p2p.bip324 import (
    HEADER_LEN,
    LENGTH_LEN,
    TAG_LEN,
    Cipher,
    contents_from_message,
    message_from_contents,
)
from ecc.curves import secp256k1
from ecc.ellswift import create

from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "Listener",
    "Peer",
]

# BIP144 and BIP339: a real peer offers both, and btclib-node's own
# handshake refuses a connection that does not (measured against
# `382a29fb`'s `p2p.callbacks.version`, "we only connect to witness
# nodes"). bitcoind is more permissive, so this is what the peer offers
# either node rather than the least either happens to demand.
_SERVICES = ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS

_USER_AGENT = b"/btclib-functional-tests:0/"

# BIP324's bound on the garbage either side sends ahead of its terminator
_MAX_GARBAGE_LEN = 4095

# Core's `EncryptedP2PState.complete_handshake`: up to this many decoy
# packets ahead of the version packet, each of 1 to `_MAX_DECOY_LEN`
# octets
_MAX_DECOYS = 10
_MAX_DECOY_LEN = 100


class Peer:
    """One p2p connection to a node, driven by hand rather than by a peer.

    :param address: `(host, port)` to dial, a `NodeAdapter.p2p_address`.
    :param magic: the four octets of the message start,
        `bitcoin_core_rpc.magic_from_chain("regtest")` for every node this
        step drives.
    :param timeout: the wait of the dial, and of every call on this peer
        given no `timeout` of its own, `send` and a bare `receive`
        included, before `--timeout-factor`'s own scaling
        (`timeout_factor.scaled`).
    :param supports_v2_p2p: open the connection with BIP324's v2
        handshake, which returns once the node's version packet is read.
        A node that speaks only v1 closes the connection, and the
        constructor raises.

    `message_count` is Core's `P2PInterface.message_count`: how many
    messages of each command `receive` has returned, those `wait_for`
    and `handshake` read and drop included. `last_message` is Core's
    `P2PInterface.last_message`: the latest message of each command
    `receive` has returned, kept whole for the caller to parse.
    """

    def __init__(
        self,
        address: tuple[str, int],
        magic: bytes,
        *,
        timeout: float = 30.0,
        supports_v2_p2p: bool = False,
    ) -> None:
        timeout = scaled(timeout)
        connection = socket.create_connection(address, timeout=timeout)
        self._adopt(connection, magic, timeout, dialled=True)
        if supports_v2_p2p:
            self._v2_handshake_or_close()

    @classmethod
    def _accepted(
        cls,
        connection: socket.socket,
        magic: bytes,
        timeout: float,
        *,
        supports_v2_p2p: bool,
    ) -> Self:
        """Return a `Peer` over a connection the node dialled, for `Listener`.

        :param timeout: already scaled, as `Listener.__init__` scales it.
        :param supports_v2_p2p: as `Listener.accept`'s.
        """
        peer = cls.__new__(cls)
        peer._adopt(connection, magic, timeout, dialled=False)
        if supports_v2_p2p:
            peer._v2_handshake_or_close()
        return peer

    def _adopt(
        self, connection: socket.socket, magic: bytes, timeout: float, *, dialled: bool
    ) -> None:
        """Take `connection` over, whichever side opened it."""
        connection.settimeout(timeout)
        self._socket = connection
        self._magic = magic
        self._timeout = timeout
        self._dialled = dialled
        self._buffer = b""
        self._cipher: Cipher | None = None
        # the length of the packet being read, once decrypted: the
        # length cipher has moved past it, so a wait that times out
        # before the rest arrives keeps it for the next read
        self._contents_len: int | None = None
        # the received garbage, which the first packet after it
        # authenticates
        self._aad = b""
        self.message_count: Counter[str] = Counter()
        self.last_message: dict[str, Message] = {}

    def _v2_handshake_or_close(self) -> None:
        """Run the v2 handshake, closing the socket where it raises."""
        try:
            self._v2_handshake()
        except BaseException:
            self.close()
            raise

    def _v2_handshake(self) -> None:
        """Run BIP324's handshake, up to the node's version packet.

        Core's `EncryptedP2PState`: the key and the garbage, the key of
        the node, then the garbage terminator, the decoys and the version
        packet. As the responder, a connection opening with v1's
        `version` header stays v1, which is Core's `respond_v2_handshake`
        answering `-1`.
        """
        if not self._dialled and self._opens_with_v1_version():
            return
        prv_key = secrets.randbelow(secp256k1.n - 1) + 1
        ell_ours = create(prv_key)
        garbage = secrets.token_bytes(secrets.randbelow(_MAX_GARBAGE_LEN + 1))
        self._socket.sendall(ell_ours + garbage)
        ell_theirs = self._take(ELL_SIZE)
        cipher = Cipher(prv_key, ell_ours, ell_theirs, self._dialled, self._magic)
        packets = [cipher.send_garbage_terminator]
        aad = garbage
        for _ in range(secrets.randbelow(_MAX_DECOYS + 1)):
            decoy = bytes(secrets.randbelow(_MAX_DECOY_LEN) + 1)
            packets.append(cipher.encrypt(decoy, aad, ignore=True))
            aad = b""
        # the version packet: BIP324 defines no contents for it yet
        packets.append(cipher.encrypt(b"", aad))
        self._socket.sendall(b"".join(packets))
        self._cipher = cipher
        self._aad = self._take_garbage(cipher.recv_garbage_terminator)
        # the node's decoys, then its version packet, whose contents
        # BIP324 has a receiver ignore
        while self._receive_packet(cipher)[1]:
            pass

    def _opens_with_v1_version(self) -> bool:
        """Return whether the node's first octets are v1's `version` header.

        Core's `respond_v2_handshake`: the magic and the command, read up
        to the first octet that differs.
        """
        prefix = Message(self._magic, "version").serialize()[: len(self._magic) + 12]
        while True:
            head = self._buffer[: len(prefix)]
            if not prefix.startswith(head):
                return False
            if head == prefix:
                return True
            self._recv_more()

    def _take_garbage(self, terminator: bytes) -> bytes:
        """Return the node's garbage, consuming its terminator after it.

        :raises ValueError: no terminator within BIP324's bound on the
            garbage, the case Core's `authenticate_handshake` disconnects on.
        """
        limit = _MAX_GARBAGE_LEN + len(terminator)
        while (end := self._buffer.find(terminator, 0, limit)) < 0:
            if len(self._buffer) >= limit:
                err_msg = f"no garbage terminator within {_MAX_GARBAGE_LEN} octets"
                raise ValueError(err_msg)
            self._recv_more()
        garbage = self._buffer[:end]
        self._buffer = self._buffer[end + len(terminator) :]
        return garbage

    def _recv_more(self) -> None:
        """Append what the socket holds next to the buffer.

        :raises ConnectionError: the node closed the connection.
        """
        if not (chunk := self._socket.recv(4096)):
            err_msg = "connection closed while waiting for a message"
            raise ConnectionError(err_msg)
        self._buffer += chunk

    def _take(self, size: int) -> bytes:
        """Return the next `size` octets, reading the socket as needed.

        A wait that times out takes nothing: what arrived stays in the
        buffer.
        """
        while len(self._buffer) < size:
            self._recv_more()
        taken = self._buffer[:size]
        self._buffer = self._buffer[size:]
        return taken

    def _receive_packet(self, cipher: Cipher) -> tuple[bytes, bool]:
        """Return the next v2 packet's contents, and whether it is a decoy.

        :raises BTClibValueError: the packet failed authentication; BIP324
            has the connection closed on it.
        """
        if self._contents_len is None:
            self._contents_len = cipher.decrypt_length(self._take(LENGTH_LEN))
        ciphertext = self._take(HEADER_LEN + self._contents_len + TAG_LEN)
        self._contents_len = None
        aad, self._aad = self._aad, b""
        return cipher.decrypt(ciphertext, aad)

    @property
    def supports_v2_p2p(self) -> bool:
        """Whether this connection speaks BIP324's v2 transport.

        Core's `P2PConnection.supports_v2_p2p`. A connection the node
        opened with v1's `version` answers `False`, though it was
        accepted with `supports_v2_p2p`.
        """
        return self._cipher is not None

    def close(self) -> None:
        """Close the underlying socket."""
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def is_connected(self) -> bool:
        """Whether the connection is still open, read without waiting.

        Core's `P2PInterface.is_connected`, which reads the state its
        background thread keeps. With no such thread here, this reads
        whatever the socket already holds without blocking, keeping the
        octets for `receive`, and answers `False` where that read ends in
        the node's close or reset, or where `close` has run.
        """
        if self._socket.fileno() == -1:
            return False
        timeout = self._socket.gettimeout()
        self._socket.settimeout(0.0)
        try:
            while chunk := self._socket.recv(4096):
                self._buffer += chunk
        except BlockingIOError:
            return True
        except ConnectionError:
            return False
        finally:
            self._socket.settimeout(timeout)
        return False

    def send(
        self, payload: Payload, *, check_validity: bool = True, is_decoy: bool = False
    ) -> None:
        """Frame `payload` for this connection's own network, and send it.

        :param payload: the message to send.
        :param check_validity: forwarded to `Payload.to_message`.
            `False` is what `p2p_invalid_locator`'s own subject needs: a
            locator over `MAX_LOCATOR_SZ` is exactly the octets a node's
            own refusal is asked about, and `assert_valid` refuses to
            build one at all where this stayed `True`.
        :param is_decoy: send the message as a v2 packet the node is to
            ignore, Core's `send_without_ping(..., is_decoy=True)`.
        :raises ValueError: `is_decoy` on a v1 connection, which has no
            such packet.
        """
        message = payload.to_message(self._magic, check_validity=check_validity)
        if self._cipher is None:
            if is_decoy:
                err_msg = "a decoy is a v2 packet, and this connection speaks v1"
                raise ValueError(err_msg)
            self._socket.sendall(message.serialize())
            return
        contents = contents_from_message(message.command, message.payload)
        self._socket.sendall(self._cipher.encrypt(contents, ignore=is_decoy))

    def send_raw(self, data: bytes) -> None:
        """Send octets as they are, past this peer's framing and encryption.

        `send` above always frames for `self._magic`, this connection's
        own network; a caller building a message with a *different*
        magic on purpose -- the log family's own tests, provoking the
        node's "wrong network" refusal -- serializes it directly
        (`payload.to_message(bad_magic).serialize()`) and hands the
        octets here instead.
        """
        self._socket.sendall(data)

    def receive(self, *, timeout: float | None = None) -> Message:
        """Return the next whole message, reading more off the socket as needed.

        :param timeout: applied to the underlying socket for every read
            this call makes, and for this call only: the socket is back
            at this peer's own default wait when it returns or raises.
            That default wait where `None`.
        :raises ConnectionError: the peer closed the connection.
        :raises TimeoutError: no octet arrived within `timeout`.
        :raises ValueError: on a v1 connection, octets that do not open
            with this connection's magic, as a v2 handshake's key does
            not; on a v2 connection, a packet that fails authentication.
        """
        if timeout is None:
            return self._receive()
        self._socket.settimeout(timeout)
        try:
            return self._receive()
        finally:
            self._socket.settimeout(self._timeout)

    def _receive(self) -> Message:
        """Return the next whole message, at the socket's current timeout."""
        cipher = self._cipher
        message = self._receive_v1() if cipher is None else self._receive_v2(cipher)
        self.message_count[message.command] += 1
        self.last_message[message.command] = message
        return message

    def _receive_v1(self) -> Message:
        """Return the next v1 message.

        The magic is checked first, as Core's `P2PConnection._on_data`
        does: a v2 key raises here, the caller closes the connection, and a
        v2 node then dials it again over v1.
        """
        while True:
            head = self._buffer[: len(self._magic)]
            if len(head) == len(self._magic) and head != self._magic:
                err_msg = f"magic bytes mismatch: {head.hex()} instead of "
                err_msg += self._magic.hex()
                raise ValueError(err_msg)
            stream = io.BytesIO(self._buffer)
            try:
                message = Message.parse(stream)
            except IncompleteMessageError:
                self._recv_more()
            else:
                self._buffer = self._buffer[stream.tell() :]
                return message

    def _receive_v2(self, cipher: Cipher) -> Message:
        """Return the next v2 message, decoys dropped.

        A message whose type `message_from_contents` refuses is dropped,
        and the connection kept, as Core's node does.
        """
        while True:
            contents, ignore = self._receive_packet(cipher)
            if ignore:
                continue
            try:
                command, payload = message_from_contents(contents)
            except BTClibValueError:
                continue
            return Message(self._magic, command, payload)

    def wait_for(
        self,
        command: str,
        *,
        predicate: Callable[[Message], bool] | None = None,
        timeout: float | None = None,
    ) -> Message:
        """Return the next `command` message, answering a `ping` meanwhile.

        Every other message received while waiting is read and dropped,
        `ping` excepted: it is answered with the nonce it carried, so a
        caller waiting on anything else is never the one that has to
        keep the connection's own keepalive alive.

        :param command: the message name to wait for, `"version"` or
            `"block"`.
        :param predicate: an additional test the message must pass,
            beyond naming `command` -- matching a `pong`'s own nonce,
            say. Every message failing it is dropped exactly as one of
            the wrong command is.
        :param timeout: how long to wait, scaled by `--timeout-factor`
            like every other explicit wait; `self._timeout` (scaled
            already, at construction) where `None`.
        :raises TimeoutError: no matching message arrived in time.
        """
        deadline = time.monotonic() + (
            self._timeout if timeout is None else scaled(timeout)
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                message = self.receive(timeout=remaining)
            except TimeoutError:
                break
            if message.command == "ping":
                self.send(Pong(Ping.parse(message.payload).nonce))
            if message.command == command and (predicate is None or predicate(message)):
                return message
        err_msg = f"never saw {command!r} within the wait"
        raise TimeoutError(err_msg)

    def wait_for_disconnect(self, *, timeout: float | None = None) -> None:
        """Block until the node closes this connection, or raise.

        The log family's own wire-observable half: a disconnect is a
        `ConnectionError` out of `receive`, so this drains and drops
        whatever the node still sends first -- a `ping`, an `addr` --
        exactly as `wait_for` does, until either the socket closes or the
        deadline passes with it still open.

        :param timeout: how long to wait, scaled by `--timeout-factor`
            like every other explicit wait; `self._timeout` (scaled
            already, at construction) where `None`.
        :raises AssertionError: the connection was still open at the
            deadline.
        """
        deadline = time.monotonic() + (
            self._timeout if timeout is None else scaled(timeout)
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self.receive(timeout=remaining)
            except ConnectionError:
                return
            except TimeoutError:
                break
        err_msg = "connection was not closed within the wait"
        raise AssertionError(err_msg)

    def sync_with_ping(self, *, timeout: float | None = None) -> None:
        """Send two `ping`s, and wait for the `pong` answering the second.

        A node processing a connection's messages in the order they
        arrive, as Core's does, answers the second `ping` only once every
        message sent ahead of the pings has been processed, and processes
        the first `ping` as a message of its own before it, so it runs its
        own send loop for this connection at least once in between. A node
        answering a `ping` ahead of the messages sent before it
        ([ISS btclib-node#1410](https://github.com/btclib-org/btclib-node/issues/1410))
        guarantees neither. Core's
        `P2PInterface.sync_with_ping` is the same barrier, built the same
        way, and its `TestNode.add_p2p_connection` runs it after the
        handshake: `handshake` returns on the node's own `verack`, which
        the node sends before it has processed this peer's.

        :param timeout: how long to wait, scaled by `--timeout-factor`
            like every other explicit wait; `self._timeout` (scaled
            already, at construction) where `None`.
        :raises TimeoutError: no matching `pong` arrived in time.
        """
        # nonzero, so that the answer to the first ping never matches
        nonce = secrets.randbelow(2**64 - 1) + 1
        self.send(Ping(0))
        self.send(Ping(nonce))
        self.wait_for(
            "pong",
            predicate=lambda m: Pong.parse(m.payload).nonce == nonce,
            timeout=timeout,
        )

    def handshake(
        self,
        *,
        services: ServiceFlags = _SERVICES,
        addrv2: bool = False,
        wtxidrelay: bool = True,
    ) -> Version:
        """Exchange `version`/`verack`, and return the node's own `version`.

        Core's own handshake. The side that dialled sends its `version`
        first: this peer where it dialled the node, the node where it
        dialled a `Listener`, this peer then answering the node's own
        `version` with its own, as Core's `P2PInterface.on_version` does.
        Then, once both `version`s are out, BIP339's `wtxidrelay` goes
        ahead of this peer's `verack` -- a `btclib-node` build before
        btclib-org/btclib-node#1183 refuses a `verack` that arrives
        without it first (measured against `382a29fb`'s
        `p2p.callbacks.verack`, "a `verack` ahead of the
        `version`/`wtxidrelay` it depends on"), and bitcoind accepts one
        either way, so sending it is what one peer can offer both nodes.

        :param services: the services this peer's own `version` offers,
            `NODE_NETWORK | NODE_WITNESS` where not given. Core's
            `add_p2p_connection` takes the same `services`, and
            `rpc_getblockfrompeer` drops `NODE_WITNESS` from it to be a
            pre-segwit peer.
        :param addrv2: send BIP155's `sendaddrv2` between the `wtxidrelay`
            and the `verack`, asking the node for `addrv2` rather than
            `addr`: Core's own `P2PInterface(support_addrv2=True)`, whose
            `on_version` sends it there. BIP155 has it sent before
            `verack`, and bitcoind drops a peer sending it after.
        :param wtxidrelay: send BIP339's `wtxidrelay`, `True` where not
            given. `False` is Core's own `P2PInterface(wtxidrelay=False)`,
            a peer announcing and asked for transactions by txid, which
            a btclib-node build before btclib-org/btclib-node#1183
            refuses, as above.
        :returns: the node's own `Version`, `nServices` and all --
            `p2p_getdata`'s own `P2PStoreBlock` reads nothing off it, but
            a later family well might.
        """
        nonce = secrets.randbelow(2**64)
        version = Version(services=services, user_agent=_USER_AGENT, nonce=nonce)
        if self._dialled:
            self.send(version)
            version_message = self.wait_for("version")
        else:
            version_message = self.wait_for("version")
            self.send(version)
        if wtxidrelay:
            self.send(WtxidRelay())
        if addrv2:
            self.send(SendAddrV2())
        self.send(Verack())
        self.wait_for("verack")
        return Version.parse(version_message.payload)


class Listener:
    """A loopback socket a node is made to dial, each connection a `Peer`.

    Core's `P2PConnection.peer_accept_connection`, what its
    `TestNode.add_outbound_p2p_connection` listens with, over one blocking
    socket rather than an event loop. The contract:

    - bound at construction to `127.0.0.1` and a port the OS chooses,
      and listening from then on, so `address` is dialable before the
      caller asks the node to dial it;
    - a backlog of one: the kernel completes the node's own connection
      and queues it until `accept`, so the call that makes the node dial
      and the `accept` after it run in sequence on the caller's thread;
    - `accept` returns the next queued connection as a `Peer` whose
      `handshake` waits for the node's `version` before sending its own,
      and whose waits are this listener's `timeout`;
    - `close` stops listening and leaves every accepted `Peer` open.

    :param magic: the four octets of the message start, as `Peer`'s.
    :param timeout: how long `accept` waits, and every accepted `Peer`'s
        default wait, before `--timeout-factor`'s own scaling
        (`timeout_factor.scaled`).
    """

    def __init__(self, magic: bytes, *, timeout: float = 30.0) -> None:
        self._magic = magic
        self._timeout = scaled(timeout)
        # a timeout `settimeout` refuses closes the socket before raising
        with contextlib.ExitStack() as acquired:
            self._socket = acquired.enter_context(
                socket.create_server(("127.0.0.1", 0), backlog=1)
            )
            self._socket.settimeout(self._timeout)
            acquired.pop_all()

    @property
    def address(self) -> tuple[str, int]:
        """Return `(host, port)` this listener is bound to."""
        host, port = self._socket.getsockname()[:2]
        return str(host), int(port)

    def accept(self, *, supports_v2_p2p: bool = False) -> Peer:
        """Return the next connection a node made to `address`, as a `Peer`.

        :param supports_v2_p2p: answer a node opening with BIP324's v2
            handshake, and return once its version packet is read. A
            node opening with v1's `version` gets a v1 `Peer`.
        :raises TimeoutError: no connection arrived within the wait.
        """
        connection, _ = self._socket.accept()
        return Peer._accepted(
            connection, self._magic, self._timeout, supports_v2_p2p=supports_v2_p2p
        )

    def close(self) -> None:
        """Stop listening; a `Peer` already accepted stays open."""
        self._socket.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
