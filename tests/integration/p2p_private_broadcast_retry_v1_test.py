# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_private_broadcast_retry_v1`, one body over each node.

Read from Core's `test/functional/p2p_private_broadcast_retry_v1.py`
(`4e8c4bc794c0`, 2026-08-04): a private broadcast connection to an IPv4
address goes through the Tor proxy, and where its v2 handshake fails, the
v1 retry to the same address goes through the Tor proxy too.

The node is given a `socks5.Socks5Proxy` for every network, `-proxy`,
and another for Tor, `-onion`, as Core's is. The first forwards each
connection to a `_V2Peer`, a v1 peer whose `version` offers
`NODE_P2P_V2`, until the restart, so that the node's address manager
marks each IPv4 address as speaking v2. The second forwards the first
IPv4 address it is asked for, and every later request for that address,
to a `_Sniffer`, which reads the start of what the node sends and
closes: v1's network magic, or the start of v2's key.

The body asks for `Capability.PRIVATE_BROADCAST`, and for the capability of
every other option its node is given: `PROXY` for `-proxy` and `-onion`,
`V2TRANSPORT` for `-v2transport`, `ADDRESS_TABLES` for `-test=addrman`, and
`PEER_TIMEOUT` for the `-peertimeout` Core's harness gives every node;
`KNOWN_ADDRESSES` for Core's own `fill_node_addrman`
(`test_framework/test_framework.py`) and `MINE` for its `MiniWallet`. The
`-dnsseed=0` of Core's `write_config` is passed with no capability of its own.
Every step of Core's file is kept in its order.

What differs from Core's file:

- Core's node starts on its harness's cached chain. Here `MiniWallet`
  mines a coin to maturity first.
- The Tor proxy closes every connection to an IPv4 address other than the
  first, where Core forwards each to a peer answering in v2. The node
  retries each in v1, and the retry is closed too. Nothing Core's file
  reads is on those connections.

Each `p2p_private_broadcast_retry_v1_<node>_test.py` module beside this
one runs it, `tests/integration/conftest.py`'s own module docstring
having how.
"""

from __future__ import annotations

import socket
import threading
from contextlib import ExitStack, suppress
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import Ping, Pong, ServiceFlags
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet
from btclib_functional_tests.node import free_ports, wait_until
from btclib_functional_tests.peer import Listener, Peer
from btclib_functional_tests.socks5 import Socks5Proxy
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.socks5 import Socks5Request
    from tests.conftest import AdapterFactory

__all__ = ["v1_retry_goes_through_the_tor_proxy"]

_MAGIC = magic_from_chain("regtest")

# Core's own `P2P_SERVICES` (`test_framework/p2p.py`), and `NODE_P2P_V2`
_V2_SERVICES = (
    ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS | ServiceFlags.NODE_P2P_V2
)

# Core's own `wait_until` default, in seconds
_WAIT = 60.0

# Core's own `fill_node_addrman` table, the IPv4 addresses this file asks for
_IPV4_ADDRESSES = tuple(f"{n}.0.0.1" for n in range(20, 211, 10))

# the Tor address Core's file has the node dial, and its port
_ONION = "testonlyad777777777777777777777777777777777777777775b6qd.onion:1234"

# what the start of a connection says its transport is
_V1 = 1
_V2 = 2


def _wake(address: tuple[str, int]) -> None:
    """Connect to `address` and close, waking a thread blocked in `accept`.

    What `Socks5Proxy.close` (`socks5.py`) does, as Core's own
    `Socks5Server.stop` does.
    """
    with suppress(OSError), socket.create_connection(address):
        pass


class _V2Peer:
    """Core's `P2PInterface` offering `NODE_P2P_V2`.

    Started as Core's `start_p2p_listener` starts it: a `Listener`
    accepting once on a thread of its own, answering the node's `version`
    with its own and each `ping` with a `pong` until the node closes the
    connection, in v1.
    """

    def __init__(self) -> None:
        self._listener = Listener(_MAGIC)
        self._address = self._listener.address
        self._peer: Peer | None = None
        self._closing = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def address(self) -> tuple[str, int]:
        """Return the `(host, port)` the proxy forwards to."""
        return self._address

    def close(self) -> None:
        """Stop listening, close the connection, and join the thread.

        A connection to the listener wakes a thread blocked in `accept`,
        which closing the socket does not do on Linux.
        """
        self._closing.set()
        _wake(self.address)
        self._listener.close()
        if self._peer is not None:
            self._peer.close()
        self._thread.join(scaled(_WAIT))

    def _run(self) -> None:
        """Accept the connection, then answer it until it closes.

        A node opening it in v2 sends what no message parses as, which
        ends the connection as the node's own close does.
        """
        try:
            with self._listener:
                peer = self._listener.accept()
        except OSError:
            return
        self._peer = peer
        if self._closing.is_set():
            peer.close()
            return
        try:
            peer.handshake(services=_V2_SERVICES)
            while True:
                try:
                    message = peer.receive()
                except TimeoutError:
                    continue
                if message.command == "ping":
                    peer.send(Pong(Ping.parse(message.payload).nonce))
        except (OSError, ValueError):
            peer.close()


class _Sniffer:
    """Core's `P2PDetermineV2or1AndClose`, for every connection in turn.

    A loopback socket accepting on a thread of its own. Of each connection
    it reads as many octets as the network magic holds, and closes it:
    `versions` gets `_V1` where they are the magic, `_V2` otherwise.
    """

    def __init__(self) -> None:
        self.versions: list[int] = []
        self._closing = threading.Event()
        self._socket = socket.create_server(("127.0.0.1", 0))
        host, port = self._socket.getsockname()[:2]
        self._address = str(host), int(port)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    @property
    def address(self) -> tuple[str, int]:
        """Return the `(host, port)` the proxy forwards to."""
        return self._address

    def close(self) -> None:
        """Stop accepting, and join the thread, woken as `_V2Peer`'s is."""
        self._closing.set()
        _wake(self.address)
        self._socket.close()
        self._thread.join(scaled(_WAIT))

    def _run(self) -> None:
        """Accept connections until `close`, reading each one's start."""
        while True:
            try:
                connection, _ = self._socket.accept()
            except OSError:
                return
            if self._closing.is_set():
                connection.close()
                return
            with connection:
                connection.settimeout(scaled(_WAIT))
                start = b""
                try:
                    while len(start) < len(_MAGIC) and (
                        chunk := connection.recv(len(_MAGIC) - len(start))
                    ):
                        start += chunk
                except OSError:
                    continue
                if len(start) == len(_MAGIC):
                    self.versions.append(_V1 if start == _MAGIC else _V2)


class _Proxies:
    """Core's `destinations_factory` of each proxy, and what they keep.

    `forwarding` is whether `-proxy`'s factory still forwards, Core setting
    its own to `None`. `tracked` is the first IPv4 address the Tor proxy is
    asked for, as `host:port`. `close` closes the sniffer and every peer
    made, once the proxies calling the factories are closed.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._peers: list[_V2Peer] = []
        self.forwarding = True
        self.tracked: str | None = None
        self.sniffer = _Sniffer()

    def close(self) -> None:
        """Close the sniffer and every `_V2Peer` made."""
        self.sniffer.close()
        with self._lock:
            peers = list(self._peers)
        for peer in peers:
            peer.close()

    def _v2_peer(self) -> tuple[str, int]:
        """Return the address of a new `_V2Peer`, kept for `close`."""
        peer = _V2Peer()
        with self._lock:
            self._peers.append(peer)
        return peer.address

    def all_factory(
        self, _request: Socks5Request, _proxy_client: str
    ) -> tuple[str, int] | None:
        """Forward to a `_V2Peer`, or close once `forwarding` is off."""
        return self._v2_peer() if self.forwarding else None

    def tor_factory(
        self, request: Socks5Request, _proxy_client: str
    ) -> tuple[str, int] | None:
        """Forward the tracked IPv4 address to the sniffer, Tor to a peer.

        Any other IPv4 address is closed: the module docstring has why.
        """
        host = request.host.decode()
        requested = f"{host}:{request.port}"
        onion = host.endswith(".onion")
        with self._lock:
            if not onion and self.tracked is None:
                self.tracked = requested
            tracked = self.tracked
        if requested == tracked:
            return self.sniffer.address
        return self._v2_peer() if onion else None


def v1_retry_goes_through_the_tor_proxy(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Core's `run_test`, in its order.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    with ExitStack() as stack:
        proxies = _Proxies()
        stack.callback(proxies.close)
        all_proxy = stack.enter_context(
            Socks5Proxy(authentication=True, destinations_factory=proxies.all_factory)
        )
        tor_proxy = stack.enter_context(
            Socks5Proxy(authentication=True, destinations_factory=proxies.tor_factory)
        )
        extra_args = [
            "-privatebroadcast=1",
            f"-proxy={all_proxy.endpoint}",
            f"-onion={tor_proxy.endpoint}",
            "-test=addrman",
            "-v2transport=0",
            "-dnsseed=0",
            "-peertimeout=999999999",
        ]
        rpc_port, p2p_port = free_ports(2)
        node = make_adapter(
            cls, executable, tmp_path / "node0", rpc_port, p2p_port, extra_args
        )
        for capability in (
            Capability.PRIVATE_BROADCAST,
            Capability.ADDRESS_TABLES,
            Capability.PROXY,
            Capability.V2TRANSPORT,
            Capability.PEER_TIMEOUT,
            Capability.KNOWN_ADDRESSES,
            Capability.MINE,
        ):
            require(capability, node.capabilities, skip_counts)
        stack.callback(node.stop)
        node.start()

        # Core's cached chain: a mature coin
        wallet = MiniWallet(node)
        wallet.generate(COINBASE_MATURITY + 1)

        # Core's `fill_node_addrman`, IPv4 alone
        for address in _IPV4_ADDRESSES:
            node.rpc.call("addpeeraddress", [address, 8333, False])

        # a v1 connection to each IPv4 address, so each reports `NODE_P2P_V2`
        for entry in node.rpc.call("getnodeaddresses", [0, "ipv4"]):
            target = f"{entry['address']}:{entry['port']}"
            node.rpc.call("addnode", [target, "onetry", False])

        def all_report_v2() -> bool:
            entries = node.rpc.call("getnodeaddresses", [0, "ipv4"])
            return all(e["services"] & ServiceFlags.NODE_P2P_V2 for e in entries)

        wait_until(all_report_v2, timeout=_WAIT)

        # the peers behind `-proxy` speak no v2: stop forwarding to them
        proxies.forwarding = False
        node.restart([*extra_args, "-v2transport=1"])

        # one Tor connection, so the node takes `-onion` for a Tor proxy
        node.rpc.call("addnode", [_ONION, "onetry", False])
        wait_until(
            lambda: any(p["network"] == "onion" for p in node.rpc.call("getpeerinfo")),
            timeout=_WAIT,
        )

        # private broadcast connections
        tx = wallet.create_self_transfer()
        node.rpc.call("sendrawtransaction", [tx.serialize(True).hex()])

        # the Tor proxy sees an IPv4 address, first in v2, then in v1
        wait_until(lambda: proxies.tracked is not None, timeout=_WAIT)
        versions = proxies.sniffer.versions
        wait_until(lambda: _V2 in versions, timeout=_WAIT)
        wait_until(lambda: _V1 in versions, timeout=_WAIT)
