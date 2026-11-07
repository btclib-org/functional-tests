# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_eviction`, one body over each node.

Read from Core's `test/functional/p2p_eviction.py` (`fa9a9657506d`,
2026-10-15), the option and MiniWallet families together
([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)):
once `-maxconnections` leaves no inbound slot for a new peer, the node
evicts an existing inbound one (`Capability.INBOUND_EVICTION`), and never
one of those its eviction logic protects -- here the peers that sent it
a novel block, those that sent it a transaction, and those with the
lowest ping. `MiniWallet` (`Capability.MINE`) funds the transactions and
`build_fork` builds the blocks.

Core's own claim in full: twenty-one inbound peers fill the slots Core's
own arithmetic leaves for transaction-relaying peers, the twenty-second
triggers the eviction, exactly one peer is disconnected, and it is none
of the peers expected to be protected. What fills those slots differs
between builds, and is read from the build (ISS 35): Core's
`1b76e0473647` lets transaction-relaying peers take only half of a
node's inbound slots, where a release before `v32.0` gives every inbound
slot to any peer. `-maxconnections=53` leaves twenty-one
transaction-relaying slots under the split, `-maxconnections=32`
twenty-one slots without it, the value Core's own file carried before
`1b76e0473647`, and `_splits_inbound_slots` below asks the node's own
`-help` which of the two it is -- `-inboundrelaypercent`, whose default
keeps that commit's half, or that commit's own wording of the split. The
node starts without `-maxconnections` and is restarted with the value
the probe picks, Core's own `extra_args` given at a restart.

`Peer` answers a ping only while a caller is reading, where Core's
`P2PInterface` answers from a background thread, so `_sync_with_ping`
below reads for it: it answers the node's first ping, after Core's own
tenth of a second for a slow peer and at once for a fast one, and it is
the `sync_with_ping` barrier Core's `add_p2p_connection` runs after the
handshake, which returns on the node's own `verack` before the node has
processed this peer's. Core sends a transaction without waiting for it;
this waits until the node's `getrawmempool` lists it before the next
peer connects, which that barrier would not ensure of a node answering a
`ping` ahead of the `tx` its peer sent first
([ISS btclib-node#1410](https://github.com/btclib-org/btclib-node/issues/1410)).

Which peer was evicted is read off `Peer.is_connected`, Core's own
`is_connected`, which waits for nothing. Core sweeps every peer until
one reads disconnected, because bitcoind's `AttemptToEvictConnection`
(`src/net.cpp`) only marks the peer, and its socket is closed on a
later pass of the node's own network loop. `_evicted` below is that
wait, within a scaled bound, and returns the sweep that ends it.

Each `p2p_eviction_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how, each
handing it the argv that prints its own node's `-help`.
"""

from __future__ import annotations

import secrets
import subprocess
import time
from functools import lru_cache
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import Headers, Ping, Pong
from btclib.p2p.data import BlockPayload, TxPayload
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet, build_fork
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Peer
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["the_evicted_inbound_peer_is_never_a_protected_one"]

_MAGIC = magic_from_chain("regtest")

# twenty-one transaction-relaying inbound slots either way: (53 - 11) / 2
# where a node splits its inbound slots by relay, 32 - 11 where it does
# not, eleven being the outbound and feeler slots Core reserves
_MAX_CONNECTIONS_SPLIT = 53
_MAX_CONNECTIONS = 32

# what `-help` prints on a build that splits its inbound slots by relay
# (`src/init.cpp`): from `0bd3d3dfa5` on, the option that sets the share,
# never wrapped; at `1b76e0473647` itself, a clause of `-maxconnections`'s
# own description that `FormatParagraph` may break across lines, matched
# with the output's whitespace collapsed
_SPLIT_OPTION = b"-inboundrelaypercent=<n>"
_SPLIT_HELP = b"percent of the remaining ones can support transaction relay"

# Core's `SlowP2PInterface`: a tenth of a second before each pong
_SLOW = 0.1

# Core's own protection counts: peers that sent a novel block, peers
# that sent a transaction, and peers protected for the lowest ping
_BLOCK_PEERS = 4
_TX_PEERS = 4
_FAST_PEERS = 8
# slow peers, the eviction candidates
_SLOW_PEERS = 5


@lru_cache
def _splits_inbound_slots(help_command: tuple[str, ...]) -> bool:
    """Return whether the node `help_command` describes splits its slots.

    A probe of the build, in the standing of `bitcoind.py`'s own
    `_has_wallet`: its `-help` names `-inboundrelaypercent`, or states
    the split in `-maxconnections`'s own description, where the build
    has one.

    :param help_command: the argv printing the node's own `-help`.
    """
    probe = subprocess.run(  # noqa: S603
        help_command, check=True, capture_output=True
    )
    assert b"-maxconnections=<n>" in probe.stdout
    return _SPLIT_OPTION in probe.stdout or _SPLIT_HELP in b" ".join(
        probe.stdout.split()
    )


def _sync_with_ping(
    peer: Peer, *, pong_delay: float, await_node_ping: bool, timeout: float = 30
) -> None:
    """Core's `sync_with_ping`, answering the node's pings on the way.

    `ping(0)` and then a nonzero nonce, and a wait for the `pong` carrying
    that nonce: from a node processing each peer's messages in order, as
    Core's does, that `pong` says everything sent before the pings has been
    processed, and from one answering a `ping` ahead of them
    ([ISS btclib-node#1410](https://github.com/btclib-org/btclib-node/issues/1410))
    it says nothing of them. Every
    `ping` the node sends meanwhile is answered after `pong_delay`; where
    `await_node_ping`, the wait also lasts until one has been, so the node
    has a ping time for this peer before the next one connects.
    """
    nonce = secrets.randbelow(2**64 - 1) + 1
    peer.send(Ping(0))
    peer.send(Ping(nonce))
    synced = False
    pinged = not await_node_ping
    deadline = time.monotonic() + scaled(timeout)
    while not (synced and pinged):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            err_msg = f"no pong for {nonce}, or no ping, within {timeout} s"
            raise TimeoutError(err_msg)
        message = peer.receive(timeout=remaining)
        if message.command == "ping":
            time.sleep(pong_delay)
            peer.send(Pong(Ping.parse(message.payload).nonce))
            pinged = True
        elif message.command == "pong":
            synced = synced or Pong.parse(message.payload).nonce == nonce


def _connect(node: NodeAdapter, peers: list[Peer], pong_delay: float) -> Peer:
    """Core's `add_p2p_connection`: handshake, then `_sync_with_ping`."""
    peer = Peer(node.p2p_address, _MAGIC)
    peers.append(peer)
    peer.handshake()
    _sync_with_ping(peer, pong_delay=pong_delay, await_node_ping=True)
    return peer


def _wait_until_tip(node: NodeAdapter, block_hash: str, timeout: float = 30) -> None:
    deadline = time.monotonic() + scaled(timeout)
    while node.rpc.call("getbestblockhash") != block_hash:
        if time.monotonic() > deadline:
            err_msg = f"the tip never reached {block_hash} within {timeout} s"
            raise TimeoutError(err_msg)
        time.sleep(0.1)


def _wait_until_in_mempool(node: NodeAdapter, txid: str) -> None:
    wait_until(lambda: txid in node.rpc.call("getrawmempool"))


def _evicted(peers: list[Peer], timeout: float = 30) -> list[int]:
    """Return the indices of the peers the node disconnected, once any is."""
    deadline = time.monotonic() + scaled(timeout)
    while True:
        evicted = [index for index, peer in enumerate(peers) if not peer.is_connected]
        if evicted or time.monotonic() > deadline:
            return evicted
        time.sleep(0.1)


def the_evicted_inbound_peer_is_never_a_protected_one(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    help_command: Sequence[str],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `run_test`, one peer after another in Core's own order.

    :param cluster: a node's own cluster fixture.
    :param help_command: the argv printing that node's own `-help`,
        which `_splits_inbound_slots` reads.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.INBOUND_EVICTION, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    max_connections = (
        _MAX_CONNECTIONS_SPLIT
        if _splits_inbound_slots(tuple(help_command))
        else _MAX_CONNECTIONS
    )
    node.restart([f"-maxconnections={max_connections}"])
    peers: list[Peer] = []
    try:
        wallet = MiniWallet(node)
        # one matured coinbase for each transaction peer to spend, where
        # Core's own cached chain already holds them
        wallet.generate(COINBASE_MATURITY + _TX_PEERS - 1)
        protected: set[int] = set()

        # protected by sending the node a novel block, the way Core's own
        # `send_blocks_and_test` does: headers, the node's getdata, the block
        for _ in range(_BLOCK_PEERS):
            peer = _connect(node, peers, _SLOW)
            block = build_fork(node, wallet.script_pub_key, 1)[0]
            peer.send(Headers([block.header]))
            peer.wait_for("getdata")
            peer.send(
                BlockPayload(block, True, check_validity=False), check_validity=False
            )
            _sync_with_ping(peer, pong_delay=_SLOW, await_node_ping=False)
            _wait_until_tip(node, block.header.hash.hex())
            protected.add(len(peers) - 1)

        # slow-pinging peers, the eviction candidates
        for _ in range(_SLOW_PEERS):
            _connect(node, peers, _SLOW)

        # protected by sending the node a transaction
        for _ in range(_TX_PEERS):
            peer = _connect(node, peers, _SLOW)
            tx = wallet.create_self_transfer()
            peer.send(TxPayload(tx, True))
            _wait_until_in_mempool(node, tx.id.hex())
            protected.add(len(peers) - 1)

        # protected by the lowest ping: answered at once
        for _ in range(_FAST_PEERS):
            _connect(node, peers, 0)

        # the node's own ping times decide which are protected, whichever
        # peer they turn out to be; a peer with none sorts last, as Core's
        # own stand-in value does
        peer_info = sorted(node.rpc.call("getpeerinfo"), key=lambda p: p["id"])
        assert len(peer_info) == len(peers)
        pings = sorted(
            range(len(peer_info)),
            key=lambda index: peer_info[index].get("minping", float("inf")),
        )
        protected.update(pings[:_FAST_PEERS])

        # the peer that triggers the eviction
        candidates = list(peers)
        _connect(node, peers, _SLOW)
        evicted = _evicted(candidates)
        assert len(evicted) == 1, f"evicted {evicted}"
        assert evicted[0] not in protected, f"evicted {evicted}, protected {protected}"
    finally:
        for peer in peers:
            peer.close()
