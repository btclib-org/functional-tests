# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_getaddr_caching`, one body over each node.

Read from Core's `test/functional/p2p_getaddr_caching.py`
(`fa5f29774872`, 2025-12-16), an option and the clock together
([ISS 14](https://github.com/btclib-org/functional-tests/issues/14)):
a node listening on a loopback address and on two onion binds
(`Capability.LISTEN_ADDRESS`), its address table filled over
`addpeeraddress` (`Capability.KNOWN_ADDRESSES`), answers every `getaddr`
arriving through one bind with the same `MAX_ADDR_TO_SEND` addresses
while its clock (`Capability.CLOCK`) moves five minutes per round, a
different answer through each bind, and a new answer through each once
the clock has moved three days. Every check of Core's is kept, in its
order.

Core's node is started with its framework's own `-peertimeout` and
`connect=0` (`write_config`, `test_framework/util.py`); here they are
passed on the one start (`Capability.PEER_TIMEOUT`), so that the moved
clock drops no peer as inactive and the node dials none of the addresses
it is given. Core's loopback bind is its framework's own `bind=127.0.0.1`
(`bind_to_localhost_only`, `test_framework/test_framework.py`), and the
onion binds its test names keep `TestNode.start` from adding its own
(`test_framework/test_node.py`). An adapter's own `_command` may name a
`-bind` too, so the node is built from the class the caller passes,
`UnboundBitcoindAdapter` (`support/bitcoind_adapters.py`) for bitcoind leaving
it out, and all three binds are named here. Core's ports come from
`p2p_port`, unprobed; here all three come from `free_ports`.

Core's `AddrReceiver` keeps the addresses of the last `addr` message it
received, its `P2PInterface.on_version` sending the `getaddr`; here each
receiver is a `Peer` that sends it once its handshake is done, and its
answer is the `addr` message it reads, `_received_addrs` having how. Core's
`TestNode.add_p2p_connection` also finds each connection in
`getpeerinfo` and compares its user agent, a check of the framework's
own that is not made here.

Each `p2p_getaddr_caching_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import Addr, GetAddr
from btclib.p2p.limits import MAX_ADDR_TO_SEND

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import free_port, free_ports
from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from ipaddress import IPv6Address
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = ["getaddr_answers_are_cached_per_bind"]

_MAGIC = magic_from_chain("regtest")

# Core's own `MAX_PCT_ADDR_TO_SEND` (`src/net_processing.cpp`), which the
# test restates: the share of its known addresses a node answers with
_MAX_PCT_ADDR_TO_SEND = 23

# Core's own loops: the addresses given to the node, and the rounds of
# requests answered from one cache
_PEER_ADDRESSES = 10000
_ROUNDS = 5

# Core's own clock moves: five minutes, past the next scheduled `addr`
# send, and three days, past the cache's own lifetime
_TRIGGER = 5 * 60
_CACHE_EXPIRED = 3 * 24 * 60 * 60

# what Core's `write_config` (`test_framework/util.py`) gives every node
# and the adapters do not: a `peertimeout` under which the moved clock
# drops no peer as inactive, and `connect=0`, so that no address the node
# is given is dialled
_HARNESS_ARGS = ("-peertimeout=999999999", "-connect=0")

_LOCALHOST = "127.0.0.1"


def _addr_receiver(stack: ExitStack, port: int) -> Peer:
    """Core's `add_p2p_connection(AddrReceiver(), dstport=port)`.

    A handshake, the `getaddr` Core's `P2PInterface.on_version` sends, and
    the `sync_with_ping` Core's `add_p2p_connection` runs. The
    connection stays open until `stack` closes, as Core's stay open until
    its test ends.
    """
    peer = stack.enter_context(Peer((_LOCALHOST, port), _MAGIC))
    peer.handshake()
    peer.send(GetAddr())
    peer.sync_with_ping()
    return peer


def _received_addrs(peer: Peer) -> list[IPv6Address]:
    """Core's `AddrReceiver.get_received_addrs`, once its `addr` arrived.

    Each address's own IP alone, in the order the node sent them, as
    Core's `on_addr` keeps them. The `addr` is read off
    `Peer.last_message` where a wait already read it -- bitcoind sends it
    ahead of the `pong` the receiver's `sync_with_ping` waits for -- and
    waited for otherwise.
    """
    message = peer.last_message.get("addr") or peer.wait_for("addr")
    return [entry.address.ip for entry in Addr.parse(message.payload).addresses]


def getaddr_answers_are_cached_per_bind(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check one cached `getaddr` answer per bind, and a new one past its life.

    :param make_adapter: the session's own adapter factory.
    :param cls: the class to build the node from.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    rpc_port = free_port()
    port, onion_port1, onion_port2 = free_ports(3)
    node = make_adapter(cls, executable, tmp_path / "node", rpc_port, port)
    for capability in (
        Capability.LISTEN_ADDRESS,
        Capability.KNOWN_ADDRESSES,
        Capability.CLOCK,
        Capability.PEER_TIMEOUT,
    ):
        require(capability, node.capabilities, skip_counts)
    binds = (port, onion_port1, onion_port2)
    try:
        with ExitStack() as stack:
            node.restart(
                [
                    f"-bind={_LOCALHOST}:{port}",
                    f"-bind={_LOCALHOST}:{onion_port1}=onion",
                    f"-bind={_LOCALHOST}:{onion_port2}=onion",
                    *_HARNESS_ARGS,
                ]
            )
            for i in range(_PEER_ADDRESSES):
                node.rpc.call("addpeeraddress", [f"{i >> 8}.{i % 256}.1.1", 8333])

            # enough known addresses that the share a node answers with is
            # capped at `MAX_ADDR_TO_SEND`
            known = node.rpc.call("getnodeaddresses", [0])
            assert len(known) > int(MAX_ADDR_TO_SEND / (_MAX_PCT_ADDR_TO_SEND / 100))

            # the answers of the round before, one per bind
            last: list[list[IPv6Address]] = []
            mocktime = int(time.time())
            for _ in range(_ROUNDS):
                receivers = [_addr_receiver(stack, bind) for bind in binds]

                mocktime += _TRIGGER
                node.set_mock_time(mocktime)
                local, onion1, onion2 = [_received_addrs(r) for r in receivers]

                if last:
                    last_local, last_onion1, last_onion2 = last
                    # an answer through one bind is none through another
                    assert last_local != onion1
                    assert last_local != onion2
                    assert last_onion1 != onion2
                    # through one bind, the answer is the same
                    assert last_local == local
                    assert last_onion1 == onion1
                    assert last_onion2 == onion2

                last = [local, onion1, onion2]
                for response in last:
                    assert len(response) == MAX_ADDR_TO_SEND

            mocktime += _CACHE_EXPIRED
            node.set_mock_time(mocktime)

            receivers = [_addr_receiver(stack, bind) for bind in binds]
            mocktime += _TRIGGER
            node.set_mock_time(mocktime)
            responses = [_received_addrs(r) for r in receivers]

            # past the cache's lifetime, a new answer through every bind
            for before, after in zip(last, responses, strict=True):
                assert set(before) != set(after)
    finally:
        node.stop()
