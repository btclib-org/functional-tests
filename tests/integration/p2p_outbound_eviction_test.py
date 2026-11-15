# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_outbound_eviction`, as bodies over each node.

Read from Core's `test/functional/p2p_outbound_eviction.py`
(`fa5f29774872`, 2025-12-16): an outbound peer that has not announced a
block with as much work as the node's tip once `CHAIN_SYNC_TIMEOUT`
passes on the node's clock is sent a `getheaders`, and is dropped once
`HEADERS_RESPONSE_TIME` passes with no such block; a peer catching up
with the tip the timer was set against, or with the tip itself, is kept;
a full-relay peer that had announced the tip is protected and kept; a
block-relay-only one never is.

Each of Core's checks is a body over a fresh node:

- unprotected: a peer sending no headers, and one sending the header of
  the tip's parent, are each dropped; a peer lagging a block behind the
  tip but catching up with each tip the timer was set against is kept,
  over each of Core's rounds, and kept once it catches up with the tip;
- protected: a full-relay peer that announced the tip is kept after both
  timeouts, the node having mined a block past it;
- mixed: of eight full-relay peers, the four that announced the tip are
  kept, and of the four others the two catching up are kept while the
  one that sent a lagging header and the one that sent none are dropped;
- block-relay-only: a block-relay-only peer that announced the tip is
  dropped as an unprotected one is.

Every body has the node dial its peers (`Capability.TYPED_OUTBOUND`),
moves the node's clock as Core's does (`Capability.CLOCK`), and mines
(`Capability.MINE`): its own chain first, two blocks at the node's
clock where Core's node starts on its framework's cached chain, Core's
checks reading the tip and its parent alone; then each block Core's
`generateblock` mines to `raw(42)`, paid where the adapter's own `mine`
pays instead. Every body restarts its node with settings Core's own
harness gives every node (`Capability.PEER_TIMEOUT`), `_HARNESS_ARGS`
below.

Core's `wait_for_getheaders` pops the latest `getheaders` its framework's
network thread recorded, and waits for the next where that one does not
match. `Peer.last_message` holds only what a read of the connection
returned, and `Peer.sync_with_ping` reads up to the `pong` answering its
`ping`, so a `getheaders` the node sent ahead of that `pong` is recorded
by the time the body waits for it, as it is in Core. Where a peer is
protected, Core's own `getheaders` wait is met by the one the node sent
the peer on connecting, which asks from the same block, the node sending
a protected peer no other.

Each `p2p_outbound_eviction_<node>_test.py` module beside this one runs
each body, `tests/integration/conftest.py`'s own module docstring having
how.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.block.block_header import BlockHeader
from btclib.p2p import GetHeaders, Headers

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.peer import Listener
from tests.integration.support.p2p_conns import Clock

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.p2p import Message

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.peer import Peer

__all__ = [
    "block_relay_only_peer_is_not_protected",
    "lagging_unprotected_peers_are_evicted",
    "only_misbehaving_unprotected_peers_are_evicted",
    "protected_peer_is_not_evicted",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]
type _Node = NodeAdapter

_MAGIC = magic_from_chain("regtest")

# what Core's own harness starts every node with and `BitcoindAdapter`
# does not: `write_config`'s (`test_framework/util.py`) `peertimeout`, so
# that moving the clock past both timeouts does not also drop every peer
# as inactive, and `connect=0`, so that it opens no connection of its own
# once the clock has moved
_HARNESS_ARGS = ("-peertimeout=999999999", "-connect=0")

# Core's own timeouts, in seconds
_CHAIN_SYNC_TIMEOUT = 20 * 60
_HEADERS_RESPONSE_TIME = 2 * 60

# the chain each body mines first: a tip, and the parent Core's checks read
_CHAIN_LENGTH = 2

# Core's own rounds of the lagging peer catching up
_LAGGING_ROUNDS = 10

# Core's own `MAX_OUTBOUND_PEERS_TO_PROTECT_FROM_DISCONNECT`
# (`src/net_processing.cpp`), and the peers of either kind the mixed
# check adds beside the protected ones
_PROTECTED_PEERS = 4
_UNPROTECTED_PEERS = 2

# Core's own default wait, `P2PInterface`'s and `wait_until`'s
_WAIT = 60.0


def _node(cluster: _Cluster, skip_counts: SkipCounts) -> tuple[_Node, Clock]:
    """Return a fresh node on its own chain and clock, or skip."""
    (node,) = cluster(1)
    require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
    require(Capability.CLOCK, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    require(Capability.PEER_TIMEOUT, node.capabilities, skip_counts)
    node.restart(_HARNESS_ARGS)
    clock = Clock(node)
    node.mine(_CHAIN_LENGTH)
    return node, clock


def _add_outbound(stack: ExitStack, node: NodeAdapter, connection_type: str) -> Peer:
    """Core's own `add_outbound_p2p_connection(P2PInterface(), ...)`."""
    with Listener(_MAGIC) as listener:
        node.add_outbound_connection(listener.address, connection_type)
        peer = stack.enter_context(listener.accept())
    peer.handshake()
    peer.sync_with_ping(timeout=_WAIT)
    return peer


def _header(node: NodeAdapter, block_hash: str) -> BlockHeader:
    """Core's own `from_hex(CBlockHeader(), getblockheader(hash, False))`."""
    header_hex = node.rpc.call("getblockheader", [block_hash, False])
    return BlockHeader.parse(bytes.fromhex(str(header_hex)), check_validity=False)


def _tip_header(node: NodeAdapter) -> BlockHeader:
    """Return the header of `node`'s own tip."""
    return _header(node, str(node.rpc.call("getbestblockhash")))


def _send_headers_and_ping(peer: Peer, header: BlockHeader) -> None:
    """Core's own `send_and_ping(msg_headers([header]))`."""
    peer.send(Headers([header], check_validity=False), check_validity=False)
    peer.sync_with_ping(timeout=_WAIT)


def _starts_at(block_hash: bytes) -> Callable[[Message], bool]:
    """Return whether a `getheaders` locator starts at `block_hash`."""

    def predicate(message: Message) -> bool:
        return GetHeaders.parse(message.payload).locator[0] == block_hash

    return predicate


def _wait_for_getheaders(peer: Peer, block_hash: bytes) -> None:
    """Core's `wait_for_getheaders`: take the latest, or wait for the next.

    Core pops the latest `getheaders` already received before waiting,
    so a later wait on the same peer asks for a new one.
    """
    last = peer.last_message.pop("getheaders", None)
    if last is not None and _starts_at(block_hash)(last):
        return
    peer.wait_for("getheaders", predicate=_starts_at(block_hash), timeout=_WAIT)
    peer.last_message.pop("getheaders")


def _mine_block(node: _Node) -> str:
    """Core's own `generateblock(node, output="raw(42)", transactions=[])`."""
    (block_hash,) = node.mine(1)
    return block_hash


def lagging_unprotected_peers_are_evicted(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_outbound_eviction_unprotected`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, clock = _node(cluster, skip_counts)

    tip_header = _tip_header(node)
    prev_header = _header(node, tip_header.previous_block_hash.hex())

    with ExitStack() as stack:
        # a peer sending no headers is dropped: first sent a `getheaders`
        peer = _add_outbound(stack, node, "outbound-full-relay")
        clock.bump(_CHAIN_SYNC_TIMEOUT + 1)
        peer.sync_with_ping(timeout=_WAIT)
        _wait_for_getheaders(peer, tip_header.previous_block_hash)
        # then dropped once it answers none in time
        clock.bump(_HEADERS_RESPONSE_TIME + 1)
        peer.wait_for_disconnect(timeout=_WAIT)

        # a peer whose headers end at the tip's parent is dropped too
        peer = _add_outbound(stack, node, "outbound-full-relay")
        _send_headers_and_ping(peer, prev_header)
        clock.bump(_CHAIN_SYNC_TIMEOUT + 1)
        peer.sync_with_ping(timeout=_WAIT)
        _wait_for_getheaders(peer, tip_header.previous_block_hash)
        clock.bump(_HEADERS_RESPONSE_TIME + 1)
        peer.wait_for_disconnect(timeout=_WAIT)

        # a peer lagging behind, but catching up with the tip the timer
        # was set against each time, is kept
        peer = _add_outbound(stack, node, "outbound-full-relay")
        prev_prev_hash = tip_header.previous_block_hash
        best_block_hash = _mine_block(node)
        peer.sync_with_ping(timeout=_WAIT)

        for _ in range(_LAGGING_ROUNDS):
            # a block more, so the peer is two blocks behind
            prev_header = _header(node, best_block_hash)
            best_block_hash = _mine_block(node)
            tip_header = _header(node, best_block_hash)
            peer.sync_with_ping(timeout=_WAIT)

            # not enough to drop the peer
            clock.bump(_CHAIN_SYNC_TIMEOUT + 1)
            peer.sync_with_ping(timeout=_WAIT)

            # the node's last call
            _wait_for_getheaders(peer, prev_prev_hash)

            # the previous tip's header, one block behind again
            _send_headers_and_ping(peer, prev_header)
            prev_prev_hash = tip_header.previous_block_hash

        # the same peer catching up with the tip in time is kept
        _send_headers_and_ping(peer, _header(node, best_block_hash))
        clock.bump(_CHAIN_SYNC_TIMEOUT + 1)
        peer.sync_with_ping(timeout=_WAIT)
        clock.bump(_HEADERS_RESPONSE_TIME + 1)
        peer.sync_with_ping(timeout=_WAIT)


def protected_peer_is_not_evicted(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Core's own `test_outbound_eviction_protected`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, clock = _node(cluster, skip_counts)
    tip_header = _tip_header(node)

    with ExitStack() as stack:
        # a full-relay peer sharing the tip is granted protection
        peer = _add_outbound(stack, node, "outbound-full-relay")
        _send_headers_and_ping(peer, tip_header)

        _mine_block(node)
        peer.sync_with_ping(timeout=_WAIT)

        # both timeouts pass, and the peer is kept
        clock.bump(_CHAIN_SYNC_TIMEOUT + 1)
        peer.sync_with_ping(timeout=_WAIT)
        _wait_for_getheaders(peer, tip_header.previous_block_hash)
        clock.bump(_HEADERS_RESPONSE_TIME + 1)
        peer.sync_with_ping(timeout=_WAIT)


def only_misbehaving_unprotected_peers_are_evicted(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_outbound_eviction_mixed`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, clock = _node(cluster, skip_counts)

    with ExitStack() as stack:
        # the first peers are protected by announcing the tip
        tip_header = _tip_header(node)
        protected_peers = []
        for _ in range(_PROTECTED_PEERS):
            peer = _add_outbound(stack, node, "outbound-full-relay")
            _send_headers_and_ping(peer, tip_header)
            protected_peers.append(peer)

        # the rest are not: the honest ones announce the tip's parent
        prev_header = _header(node, tip_header.previous_block_hash.hex())
        honest_unprotected_peers = []
        for _ in range(_UNPROTECTED_PEERS):
            peer = _add_outbound(stack, node, "outbound-full-relay")
            _send_headers_and_ping(peer, prev_header)
            honest_unprotected_peers.append(peer)

        # of the misbehaving ones, the first does too and the second is silent
        misbehaving_unprotected_peers = []
        for i in range(_UNPROTECTED_PEERS):
            peer = _add_outbound(stack, node, "outbound-full-relay")
            if i % 2 == 0:
                _send_headers_and_ping(peer, prev_header)
            misbehaving_unprotected_peers.append(peer)

        # a block leaves every peer behind; the honest ones catch up
        target_hash = prev_header.hash
        tip_hash = _mine_block(node)
        tip_header = _header(node, tip_hash)

        clock.bump(_CHAIN_SYNC_TIMEOUT + 1)
        for peer in protected_peers + misbehaving_unprotected_peers:
            peer.sync_with_ping(timeout=_WAIT)
            _wait_for_getheaders(peer, target_hash)
        for peer in honest_unprotected_peers:
            _send_headers_and_ping(peer, tip_header)
            _wait_for_getheaders(peer, target_hash)

        # the protected and the honest are kept, the misbehaving dropped
        clock.bump(_HEADERS_RESPONSE_TIME + 1)
        for peer in protected_peers + honest_unprotected_peers:
            peer.sync_with_ping(timeout=_WAIT)
        for peer in misbehaving_unprotected_peers:
            peer.wait_for_disconnect(timeout=_WAIT)


def block_relay_only_peer_is_not_protected(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_outbound_eviction_blocks_relay_only`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, clock = _node(cluster, skip_counts)
    tip_header = _tip_header(node)

    with ExitStack() as stack:
        # sharing the tip grants a block-relay-only peer no protection
        peer = _add_outbound(stack, node, "block-relay-only")
        _send_headers_and_ping(peer, tip_header)

        _mine_block(node)
        peer.sync_with_ping(timeout=_WAIT)

        # both timeouts pass, and the peer is dropped
        clock.bump(_CHAIN_SYNC_TIMEOUT + 1)
        peer.sync_with_ping(timeout=_WAIT)
        _wait_for_getheaders(peer, tip_header.hash)
        clock.bump(_HEADERS_RESPONSE_TIME + 1)
        peer.wait_for_disconnect(timeout=_WAIT)
