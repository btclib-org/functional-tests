# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_addr_selfannouncement`, as bodies over each node.

Read from Core's `test/functional/p2p_addr_selfannouncement.py`
(`dab7f2c984bd`, 2026-07-07): a node given `-externalip`
(`Capability.EXTERNAL_IP`) advertises that address to a peer once the
connection opens, in an `addr` or `addrv2` message of its own, the first
the peer receives, and again later, each time the clock has moved far
enough.

Core's `self_announcement_test` is run by each body over `addr` and then
over `addrv2`, each body over a fresh node:

- an inbound peer, which sends `getaddr` as Core's `P2PInterface` does,
  receives the self-announcement alone in its first address message and
  the node's answer to its `getaddr` in the second, from the addresses
  the node was given over `addpeeraddress`
  (`Capability.KNOWN_ADDRESSES`);
- an outbound full-relay peer the node dials
  (`Capability.TYPED_OUTBOUND`) receives the self-announcement alone;
- each receives one more self-announcement, alone in one more message,
  each time the node's clock (`Capability.CLOCK`) moves twenty days.

Each is a wire half, asserting what the peer receives, and a log half
asserting Core's own `Advertising address` line on each announcement
besides (`Capability.DEBUG_LOG`). A last body is Core's
`test_externalip_bypasses_onlynet`: under `-onlynet=ipv4`
(`Capability.ONLYNET`), an onion `-externalip` is among
`getnetworkinfo`'s `localaddresses`, onion being unreachable.

Core's chain is the framework's cached one, out of initial block
download, before whose end a node advertises no address; here the node
mines one block (`Capability.MINE`) to leave it. The node is restarted with the
`-peertimeout` and `-connect=0` Core's harness gives every node
(`Capability.PEER_TIMEOUT`), so that a clock moved twenty days drops no
peer as inactive and the addresses it is given are dialled by no
automatic connection. Core reuses one node across its checks, resetting
its clock to the wall clock at the start of each.

`-externalip` bypasses `-onlynet` in `v32.0` and later, from
bitcoin/bitcoin@8c87e32bd3937251d6f30295cc1924048e5b74d1, which `v32.0rc1`
is the first tag to carry: a bitcoind whose `getnetworkinfo` `version`
reads older than that
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35))
leaves the onion address out of `localaddresses`, which is asserted
there instead. A `master` build between that change's merge and the
version's move to `32.99` reads older and bypasses all the same, the
constant's own comment having the commits.

Before `v31.0` a bitcoind sends the first self-announcement to an inbound
peer in the same message as its answer to the peer's `getaddr`
(bitcoin/bitcoin#34146).

No probe tells the two behaviours apart, the node having no option or RPC
for it: that build's inbound checks assert the one message, read off its own
`getnetworkinfo` `version`
([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).

Each `p2p_addr_selfannouncement_<node>_test.py` module beside this one
runs each body, `tests/integration/conftest.py`'s own module docstring
having how.
"""

from __future__ import annotations

import secrets
import time
from contextlib import ExitStack, nullcontext
from dataclasses import replace
from ipaddress import IPv4Address
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.block.block_header import BlockHeader
from btclib.p2p import (
    Addr,
    AddrV2,
    BIP155Network,
    GetAddr,
    Headers,
    NetworkAddressV2,
    Ping,
    Pong,
)
from btclib.p2p.addrv2 import peer_from_addr_entry

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Listener, Peer
from btclib_functional_tests.timeout_factor import scaled
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from btclib.p2p import Message

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "externalip_bypasses_onlynet",
    "self_announcement_to_inbound_peers",
    "self_announcement_to_inbound_peers_is_logged",
    "self_announcement_to_outbound_peers",
    "self_announcement_to_outbound_peers_is_logged",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

# Core's own constants
_IP_TO_ANNOUNCE = "42.42.42.42"
_ONION_ADDR = "pg6mmjiyjmcrsslvykfwnntlaru7p5svn6y2ymmju6nubxndf4pscryd.onion"
_ONE_DAY = 60 * 60 * 24

# Core's own loops: the addresses given to the node, and the later
# self-announcements each check waits for
_PEER_ADDRESSES = 50
_LATER_ANNOUNCEMENTS = 5

# what Core's `write_config` (`test_framework/util.py`) gives every node
# and `BitcoindAdapter` does not: a `peertimeout` under which the moved
# clock drops no peer as inactive, and `connect=0`, so that no address
# the node is given is dialled
_HARNESS_ARGS = ("-peertimeout=999999999", "-connect=0")

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which `-externalip` bypasses
# `-onlynet`: `v32.0`'s, `v32.0rc1` being the first tag carrying the change.
# A known limit: a `master` build from its merge (`70d9ec7f3d`, 2026-07-14)
# until the version moved to `32.99` (`f3fec67c3e`, 2026-09-11) reports
# `319900` and bypasses `-onlynet` all the same, so this test fails against
# such a build
_EXTERNALIP_BYPASSES_ONLYNET_VERSION = 320000

# Core's own `CLIENT_VERSION`, at or past which the first self-announcement
# to an inbound peer is alone in its message: `v31.0`'s
# (bitcoin/bitcoin#34146). A known limit: a `master` build from that
# change's merge (`80c4c2df3f`, 2026-01-14) until the version moved to `31.99`
# (`48b952cbb6`, 2026-03-06) reports `309900` and sends it alone all the same,
# so the inbound checks fail against such a build
_SEPARATE_FIRST_ANNOUNCEMENT_VERSION = 310000


def _debug_log(node: NodeAdapter, skip_counts: SkipCounts) -> Path:
    """Return the log a log half reads, or skip where the node keeps none.

    :raises TypeError: `node` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return node.debug_log_path


def _expecting(
    log_path: Path | None, expected: Sequence[str]
) -> AbstractContextManager[None]:
    """Return `assert_debug_log` over `log_path`, or nothing for a wire half."""
    if log_path is None:
        return nullcontext()
    return assert_debug_log(log_path, expected)


class _SelfAnnouncementReceiver:
    """Core's own `SelfAnnouncementReceiver`: every address message, counted.

    `Peer.wait_for` drops what it does not wait for, so `sync_with_ping`
    below reads the connection itself, handing every message it reads to
    Core's own `handle_addr_message` before it looks for the `pong`.

    :param peer: the connection, its handshake done.
    :param expected: the self-announcement, its timestamp the node's clock.
    :param addrv2: whether the handshake asked for `addrv2`, the one
        address message Core's receiver then accepts.
    :param first_alone: whether the first self-announcement is alone in
        the first address message.
    """

    def __init__(
        self,
        peer: Peer,
        expected: NetworkAddressV2,
        *,
        addrv2: bool,
        first_alone: bool = True,
    ) -> None:
        self.peer = peer
        self.expected = expected
        self.addrv2 = addrv2
        self.first_alone = first_alone
        self.self_announcements_received = 0
        self.addresses_received = 0
        self.addr_messages_received = 0

    def _handle(self, message: Message) -> None:
        """Core's `on_addr`, `on_addrv2` and `handle_addr_message`."""
        if message.command == "addrv2":
            assert self.addrv2
            addresses = list(AddrV2.parse(message.payload).addresses)
        elif message.command == "addr":
            assert not self.addrv2
            addresses = [
                peer_from_addr_entry(entry)
                for entry in Addr.parse(message.payload).addresses
            ]
        else:
            return
        self.addr_messages_received += 1
        for address in addresses:
            self.addresses_received += 1
            if address == self.expected:
                self.self_announcements_received += 1
                if self.self_announcements_received == 1:
                    # the first self-announcement is in the first address
                    # message, and alone in it where `first_alone`
                    assert self.addr_messages_received == 1
                    assert not self.first_alone or len(addresses) == 1

    def sync_with_ping(self) -> None:
        """`Peer.sync_with_ping`'s barrier, every message read handled.

        :raises TimeoutError: no matching `pong` arrived within the
            peer's default wait, scaled as `Peer`'s own.
        """
        nonce = secrets.randbelow(2**64 - 1) + 1
        self.peer.send(Ping(0))
        self.peer.send(Ping(nonce))
        deadline = time.monotonic() + scaled(30.0)
        while (remaining := deadline - time.monotonic()) > 0:
            message = self.peer.receive(timeout=remaining)
            if message.command == "ping":
                self.peer.send(Pong(Ping.parse(message.payload).nonce))
            self._handle(message)
            if message.command == "pong" and Pong.parse(message.payload).nonce == nonce:
                return
        err_msg = "never saw the pong within the wait"
        raise TimeoutError(err_msg)


def _restart(node: NodeAdapter) -> None:
    """Core's own node: `-externalip`, out of IBD, `_PEER_ADDRESSES` known."""
    node.restart([f"-externalip={_IP_TO_ANNOUNCE}", *_HARNESS_ARGS])
    node.mine(1)
    for i in range(_PEER_ADDRESSES):
        node.rpc.call("addpeeraddress", [f"{1 + i}.{i}.1.1", 8333])


def _first_alone(node: NodeAdapter, *, outbound: bool) -> bool:
    """Whether `node`'s first self-announcement is alone in its message.

    An outbound peer, queued no other address here, gets it alone from
    every build. An inbound peer gets it alone from a bitcoind at
    `_SEPARATE_FIRST_ANNOUNCEMENT_VERSION` or later, and with the `getaddr`
    answer from an older one
    ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).
    """
    version = bitcoind_version(node)
    return (
        outbound or version is None or version >= _SEPARATE_FIRST_ANNOUNCEMENT_VERSION
    )


def _connect(node: NodeAdapter, *, outbound: bool, addrv2: bool) -> Peer:
    """Core's `add_outbound_p2p_connection` or `add_p2p_connection`.

    A handshake, asking for `addrv2` where `addrv2`, and on an inbound
    connection the `getaddr` Core's `P2PInterface.on_version` sends.
    """
    if outbound:
        with Listener(_MAGIC) as listener:
            node.add_outbound_connection(listener.address, "outbound-full-relay")
            peer = listener.accept()
    else:
        peer = Peer(node.p2p_address, _MAGIC)
    try:
        peer.handshake(addrv2=addrv2)
        if not outbound:
            peer.send(GetAddr())
    except BaseException:
        peer.close()
        raise
    return peer


def _self_announcement_test(
    node: NodeAdapter, log_path: Path | None, *, outbound: bool, addrv2: bool
) -> None:
    """Core's own `self_announcement_test`.

    :param log_path: the node's own log where the check is a log half,
        each announcement's `Advertising address` line then asserted.
    """
    # the node self-announces only once out of initial block download
    assert not node.rpc.call("getblockchaininfo")["initialblockdownload"]

    netinfo = node.rpc.call("getnetworkinfo")
    port = netinfo["localaddresses"][0]["port"]
    mocktime = int(time.time())
    node.set_mock_time(mocktime)

    expected = NetworkAddressV2(
        mocktime,
        int(netinfo["localservices"], 16),
        BIP155Network.IPV4,
        IPv4Address(_IP_TO_ANNOUNCE).packed,
        port,
    )
    advertising = [f"Advertising address {_IP_TO_ANNOUNCE}:{port}"]

    first_alone = _first_alone(node, outbound=outbound)
    with ExitStack() as stack:
        with _expecting(log_path, advertising):
            peer = stack.enter_context(_connect(node, outbound=outbound, addrv2=addrv2))
            receiver = _SelfAnnouncementReceiver(
                peer, expected, addrv2=addrv2, first_alone=first_alone
            )
            # `add_p2p_connection`'s own barrier, then the check's
            receiver.sync_with_ping()
            receiver.sync_with_ping()

        # one self-announcement, to either kind of peer
        assert receiver.self_announcements_received == 1
        if outbound:
            # and nothing else to an outbound peer
            assert receiver.addr_messages_received == 1
            assert receiver.addresses_received == 1
            # announced the tip, the peer is one the node does not evict
            tip = node.rpc.call("getbestblockhash")
            header = BlockHeader.parse(
                bytes.fromhex(node.rpc.call("getblockheader", [tip, False])),
                check_validity=False,
            )
            peer.send(Headers([header], check_validity=False))
            receiver.sync_with_ping()
        else:
            # the self-announcement, then the answer to the `getaddr`,
            # or both in one message
            assert receiver.addr_messages_received == (2 if first_alone else 1)
            assert receiver.addresses_received > 1

        for _ in range(_LATER_ANNOUNCEMENTS):
            last_self_announcements = receiver.self_announcements_received
            last_addr_messages = receiver.addr_messages_received
            last_addresses = receiver.addresses_received
            with _expecting(log_path, advertising):
                # sent at intervals exponentially distributed around a day:
                # twenty days leaves one unsent once in about 500 million
                mocktime += 20 * _ONE_DAY
                receiver.expected = replace(receiver.expected, timestamp=mocktime)
                node.set_mock_time(mocktime)
                receiver.sync_with_ping()

            assert receiver.self_announcements_received == last_self_announcements + 1
            assert receiver.addr_messages_received == last_addr_messages + 1
            assert receiver.addresses_received == last_addresses + 1

    wait_until(lambda: node.rpc.call("getpeerinfo") == [])


def _require_self_announcement(node: NodeAdapter, skip_counts: SkipCounts) -> None:
    """Ask for what every self-announcement check needs of `node`."""
    for capability in (
        Capability.EXTERNAL_IP,
        Capability.KNOWN_ADDRESSES,
        Capability.MINE,
        Capability.CLOCK,
        Capability.PEER_TIMEOUT,
    ):
        require(capability, node.capabilities, skip_counts)


def self_announcement_to_inbound_peers(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half: inbound peers receive the self-announcement.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    _require_self_announcement(node, skip_counts)
    _restart(node)
    _self_announcement_test(node, None, outbound=False, addrv2=False)
    _self_announcement_test(node, None, outbound=False, addrv2=True)


def self_announcement_to_inbound_peers_is_logged(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the log half: each announcement to an inbound peer is logged.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    _require_self_announcement(node, skip_counts)
    log_path = _debug_log(node, skip_counts)
    _restart(node)
    _self_announcement_test(node, log_path, outbound=False, addrv2=False)
    _self_announcement_test(node, log_path, outbound=False, addrv2=True)


def self_announcement_to_outbound_peers(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half: outbound peers receive the self-announcement.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
    _require_self_announcement(node, skip_counts)
    _restart(node)
    _self_announcement_test(node, None, outbound=True, addrv2=False)
    _self_announcement_test(node, None, outbound=True, addrv2=True)


def self_announcement_to_outbound_peers_is_logged(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the log half: each announcement to an outbound peer is logged.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
    _require_self_announcement(node, skip_counts)
    log_path = _debug_log(node, skip_counts)
    _restart(node)
    _self_announcement_test(node, log_path, outbound=True, addrv2=False)
    _self_announcement_test(node, log_path, outbound=True, addrv2=True)


def _externalip_bypasses_onlynet(node: NodeAdapter) -> bool:
    """Whether `node` keeps an onion `-externalip` under `-onlynet=ipv4`.

    Core's own claim for every node, bitcoind before
    `_EXTERNALIP_BYPASSES_ONLYNET_VERSION` excepted: that build leaves the
    address out, read off its own `getnetworkinfo` `version`
    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
    """
    if not isinstance(node, BitcoindAdapter):
        return True
    version: int = node.rpc.call("getnetworkinfo")["version"]
    return version >= _EXTERNALIP_BYPASSES_ONLYNET_VERSION


def externalip_bypasses_onlynet(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check an onion `-externalip` is a local address under `-onlynet=ipv4`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.EXTERNAL_IP, node.capabilities, skip_counts)
    require(Capability.ONLYNET, node.capabilities, skip_counts)
    node.restart(["-onlynet=ipv4", f"-externalip={_ONION_ADDR}"])

    netinfo = node.rpc.call("getnetworkinfo")

    onion_net = next(n for n in netinfo["networks"] if n["name"] == "onion")
    assert not onion_net["reachable"]

    addrs = [a["address"] for a in netinfo["localaddresses"]]
    if _externalip_bypasses_onlynet(node):
        assert _ONION_ADDR in addrs
    else:
        assert _ONION_ADDR not in addrs
