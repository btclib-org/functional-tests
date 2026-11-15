# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_ibd_stalling`, as bodies over each node.

Read from Core's `test/functional/p2p_ibd_stalling.py` (`24628d3ae7dc`,
2026-09-14): during initial block download, a peer withholding the block
the node's download window cannot move past is a staller, and the node
reacts once the stalling timeout passes on its clock.

Each of Core's checks is a wire half and a log half, each body over a
fresh node:

- five outbound full-relay peers (`Capability.TYPED_OUTBOUND`) announce
  a chain whose first block, and a later one, every peer withholds. None
  is dropped while the chain fits the window; once it does not, the
  peer holding the first block is dropped after two seconds, the next
  after four and the one after that after eight, and once a peer sends
  the block the timeout is two seconds again and the chain connects up
  to the later withheld block. The log half asserts Core's own
  `Stall started` and `Decreased stalling timeout to 2 seconds`, and the
  absence of the first beside the second, read per-build: see below.
- a `manual` peer withholding the first block keeps its connection past
  the stalling timeout, its request is given to one of four outbound
  peers that serve every block, and the node asks it for no block until
  the cooldown has passed. The log half asserts Core's own
  `Pausing block downloads from stalling manual peer`.

Core's `P2PStaller` answers the node's `getdata` from its framework's
network thread, while the test waits on the node's RPC. Here `_Staller`
answers it on the test's own thread, and only where a wait reads its
connection. A wait for what the peers' answers bring about -- the blocks
in flight, which peer is asked for a block, the height -- first reads
each connected peer's connection up to the `pong` of a ping round trip,
answering whatever it read. `wait_for_disconnect` reads the one peer it
waits on, a wait for how many peers are connected reads each through
`Peer.is_connected` and answers nothing, and the wait of
`assert_debug_log` reads no connection. A peer the node closes during a
round trip fails the check, as it fails Core's. Core's
`num_test_p2p_connections` is `getpeerinfo`'s length, the node having no
peer of any other kind. Each body moves the node's clock
(`Capability.CLOCK`) as Core's does.

Core's harness starts every node with a `peertimeout`
(`write_config`, `test_framework/util.py`) and this port passes none:
`CConnman::InactivityCheck` (`src/net.cpp`) drops a peer that has
completed its handshake only once `TIMEOUT_INTERVAL` (`src/net.h`),
twenty minutes of the node's clock, passes without a message either way,
and neither check moves the clock that far in all.

Core runs its second check over its first check's node, restarted;
here it starts from the genesis block of a fresh one.

`addconnection` takes `manual` only past the pinned release
(bitcoin/bitcoin@4c79f3a34d003bd97824b032383ac816a4147d68), and the node
pauses a stalling `manual` peer rather than dropping it only from
bitcoin/bitcoin@698b5bbf3a0a0a52d97ae6acce2752769f14a208. Where the
build's own `help addconnection` names no `manual`
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)),
the second check's bodies assert the refusal Core's
`RPC_INVALID_PARAMETER` answers instead.

A bitcoind before `v31.0` logs `Stall started` again once the block
withheld first is sent (bitcoin/bitcoin#32179, fixed by bitcoin/bitcoin#32180).
No option or RPC tells such a build apart, so the first check's log half
reads the build's own `getnetworkinfo` `version` and, there, does not
assert `Stall started` absent
([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).

Each `p2p_ibd_stalling_<node>_test.py` module beside this one runs each body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

import pytest
from bitcoin_core_rpc import RpcError, magic_from_chain
from btclib.block.block import Block
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS
from btclib.p2p import BlockPayload, GetData, Headers
from btclib.p2p.inventory import InventoryType

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Listener
from tests.integration.p2p_add_connections_test import takes_manual
from tests.integration.support.p2p_conns import Conn
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from btclib.p2p import Message, Payload, Version

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.peer import Peer

__all__ = [
    "manual_peer_stalling_is_logged",
    "manual_peer_stalling_pauses_the_peer",
    "stalling_drops_the_staller",
    "stalling_is_logged",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

# Core's own constants
_NUM_BLOCKS = 1025
_NUM_PEERS = 5
_SECOND_STALL_INDEX = 500
_NUM_OUTBOUND_PEERS = 4
_BLOCK_DOWNLOAD_COOLDOWN = 2 * 60

# Core's own `MSG_TYPE_MASK` (`test_framework/messages.py`)
_MSG_TYPE_MASK = 0xFFFFFFFF >> 2

# Core's own default wait, `P2PInterface`'s and `wait_until`'s
_WAIT = 60.0

# Core's `RPCErrorCode::RPC_INVALID_PARAMETER` (`src/rpc/protocol.h`),
# what `addconnection` throws for a connection type it does not name
_RPC_INVALID_PARAMETER = -8

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

# `OP_TRUE`, what every coinbase built here pays
_OP_TRUE = b"\x51"

_STALL_STARTED = "Stall started"
_DECREASED = "Decreased stalling timeout to 2 seconds"
# Core's own `CLIENT_VERSION`, at or past which the stall is not logged again
# once the block withheld first is sent: `v31.0`'s (bitcoin/bitcoin#32180).
# A known limit: a `master` build from that change's merge (`1fe851a478`,
# 2025-11-10) until the version moved to `31.99` (`48b952cbb6`, 2026-03-06)
# reports `309900` and does not log it all the same, so the log half fails
# against such a build
_STALL_NOT_RELOGGED_VERSION = 310000

_PAUSING = "Pausing block downloads from stalling manual peer"


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
    log_path: Path | None, expected: Sequence[str], unexpected: Sequence[str] = ()
) -> AbstractContextManager[None]:
    """Return `assert_debug_log` over `log_path`, or nothing for a wire half."""
    if log_path is None:
        return nullcontext()
    return assert_debug_log(log_path, expected, unexpected)


def _relogs_stall(node: NodeAdapter) -> bool:
    """Whether `node` logs `Stall started` again.

    It does once the block withheld first is sent, and only a bitcoind
    before `_STALL_NOT_RELOGGED_VERSION` does, read off its own
    `getnetworkinfo` `version`
    ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).
    """
    version = bitcoind_version(node)
    return version is not None and version < _STALL_NOT_RELOGGED_VERSION


def _block_payload(block: Block) -> BlockPayload:
    """Core's own `msg_block(block)`."""
    return BlockPayload(block, include_witness=True, check_validity=False)


class _Staller(Conn):
    """Core's own `P2PStaller`: every block asked for is sent, bar some.

    The node asks for blocks whatever the test is waiting on, so `_handle`
    answers each `getdata` the connection is read for. A message is sent
    without `check_validity`.

    :param peer: the connection, its handshake done.
    :param version: the node's own `version`.
    :param block_store: every block the peer can send, by its hash.
    :param stall_blocks: the hashes of the blocks it never sends.
    """

    def __init__(
        self,
        peer: Peer,
        version: Version,
        block_store: Mapping[bytes, Block],
        stall_blocks: Collection[bytes],
    ) -> None:
        super().__init__(peer, version)
        self.block_store = block_store
        self.stall_blocks = stall_blocks
        self.getdata_requests: list[bytes] = []

    @property
    def is_connected(self) -> bool:
        """Core's own `P2PInterface.is_connected`, `Peer.is_connected`."""
        return self.peer.is_connected

    @override
    def send(self, payload: Payload) -> None:
        """Send `payload`.

        :raises ConnectionError: the node closed the connection.
        """
        self.peer.send(payload, check_validity=False)

    @override
    def _handle(self, message: Message) -> None:
        """Core's `on_getdata`, and `Conn`'s `on_ping`."""
        super()._handle(message)
        if message.command == "getdata":
            for item in GetData.parse(message.payload).items:
                self.getdata_requests.append(item.hash)
                is_block = item.type_code & _MSG_TYPE_MASK == InventoryType.MSG_BLOCK
                if is_block and item.hash not in self.stall_blocks:
                    self.send(_block_payload(self.block_store[item.hash]))


def _all_sync_send_with_ping(stallers: Sequence[_Staller]) -> None:
    """Core's own: `sync_with_ping` on every peer still connected.

    :raises ConnectionError: the node closed a peer's connection during its
        round trip.
    """
    for staller in stallers:
        if staller.is_connected:
            staller.sync_with_ping()


def _serving_wait_until(
    stallers: Sequence[_Staller], predicate: Callable[[], bool]
) -> None:
    """Core's own `wait_until`, the peers answered ahead of every poll."""

    def _served() -> bool:
        _all_sync_send_with_ping(stallers)
        return predicate()

    wait_until(_served, timeout=_WAIT)


def _is_block_requested(stallers: Sequence[_Staller], block_hash: bytes) -> bool:
    """Core's own: whether a connected peer was asked for `block_hash`."""
    return any(
        staller.is_connected and block_hash in staller.getdata_requests
        for staller in stallers
    )


def _peer_info(node: NodeAdapter) -> list[dict[str, object]]:
    """Return the node's own `getpeerinfo`."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return peers


def _inflight(node: NodeAdapter, connection_type: str | None = None) -> int:
    """Return how many blocks the node has in flight, from which peers.

    :param connection_type: the peers counted, every one where `None`.
    """
    total = 0
    for peer in _peer_info(node):
        if connection_type is None or peer["connection_type"] == connection_type:
            inflight = peer["inflight"]
            assert isinstance(inflight, list)
            total += len(inflight)
    return total


def _block(previous: bytes, height: int, block_time: int) -> Block:
    """Core's own `create_block(previous, height=height, ntime=block_time)`."""
    coinbase = build_coinbase(height, _OP_TRUE, halving_interval=_HALVING_INTERVAL)
    candidate = build_block(
        previous,
        [coinbase],
        datetime.fromtimestamp(block_time, UTC),
        REGTEST_POW_LIMIT_BITS,
    )
    solved = mine(candidate.header)
    assert solved is not None
    return Block(solved, candidate.transactions, check_validity=False)


def _build_chain(node: NodeAdapter, length: int) -> list[Block]:
    """Core's own loop: `length` blocks on the node's tip, a second apart."""
    best_hash = node.rpc.call("getbestblockhash")
    assert isinstance(best_hash, str)
    height = node.rpc.call("getblockcount")
    assert isinstance(height, int)
    best = node.rpc.call("getblock", [best_hash])
    assert isinstance(best, dict)
    tip = bytes.fromhex(best_hash)
    block_time = best["time"] + 1
    blocks = []
    for _ in range(length):
        height += 1
        blocks.append(_block(tip, height, block_time))
        tip = blocks[-1].header.hash
        block_time += 1
    return blocks


def _add_outbound(
    node: NodeAdapter,
    connection_type: str,
    block_store: Mapping[bytes, Block],
    stall_blocks: Collection[bytes],
) -> _Staller:
    """Core's own `add_outbound_p2p_connection(P2PStaller(stall_blocks))`."""
    with Listener(_MAGIC) as listener:
        node.add_outbound_connection(listener.address, connection_type)
        peer = listener.accept()
    try:
        staller = _Staller(peer, peer.handshake(), block_store, stall_blocks)
        staller.sync_with_ping()
    except BaseException:
        peer.close()
        raise
    return staller


def _node(
    cluster: _Cluster, skip_counts: SkipCounts, *, log: bool
) -> tuple[NodeAdapter, Path | None]:
    """Return a fresh node, and its log for a log half, or skip.

    :param log: whether the body is a log half, asking for the node's log.
    """
    (node,) = cluster(1)
    require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
    require(Capability.CLOCK, node.capabilities, skip_counts)
    log_path = _debug_log(node, skip_counts) if log else None
    return node, log_path


def _stalling(node: NodeAdapter, log_path: Path | None) -> None:
    """Core's own `test_stalling`.

    :param log_path: the node's own log where the check is a log half.
    """
    blocks = _build_chain(node, _NUM_BLOCKS)
    block_dict = {block.header.hash: block for block in blocks}
    stall_blocks = [blocks[0].header.hash, blocks[_SECOND_STALL_INDEX].header.hash]

    headers_message = Headers([block.header for block in blocks[: _NUM_BLOCKS - 1]])
    peers: list[_Staller] = []
    try:
        # a staller is not dropped while the 1024-block window is filled
        mocktime = int(time.time()) + 1
        node.set_mock_time(mocktime)
        for _ in range(_NUM_PEERS):
            peers.append(
                _add_outbound(node, "outbound-full-relay", block_dict, stall_blocks)
            )
            peers[-1].send_and_ping(headers_message)

        # every block but the withheld ones received, nothing else in flight
        _serving_wait_until(peers, lambda: _inflight(node) == len(stall_blocks))

        _all_sync_send_with_ping(peers)
        # a peer marked as a staller would be dropped by now
        mocktime += 3
        node.set_mock_time(mocktime)
        _all_sync_send_with_ping(peers)
        assert len(_peer_info(node)) == _NUM_PEERS

        # a window beyond 1024 blocks starts the stalling logic
        headers_message = Headers([block.header for block in blocks])
        with _expecting(log_path, [_STALL_STARTED]):
            for peer in peers:
                peer.send(headers_message)
            _all_sync_send_with_ping(peers)

        # the staller is dropped after two seconds
        mocktime += 3
        node.set_mock_time(mocktime)
        peers[0].wait_for_disconnect()
        assert len(_peer_info(node)) == _NUM_PEERS - 1
        _serving_wait_until(peers, lambda: _is_block_requested(peers, stall_blocks[0]))
        # the node assigns the block to another peer, which starts stalling
        _all_sync_send_with_ping(peers)

        # the timeout doubles to four seconds for the next staller
        mocktime += 3
        node.set_mock_time(mocktime)
        _all_sync_send_with_ping(peers)
        assert len(_peer_info(node)) == _NUM_PEERS - 1

        mocktime += 2
        node.set_mock_time(mocktime)
        wait_until(
            lambda: sum(peer.is_connected for peer in peers) == _NUM_PEERS - 2,
            timeout=_WAIT,
        )
        _serving_wait_until(peers, lambda: _is_block_requested(peers, stall_blocks[0]))
        _all_sync_send_with_ping(peers)

        # the timeout doubles to eight seconds for the next staller
        mocktime += 7
        node.set_mock_time(mocktime)
        _all_sync_send_with_ping(peers)
        assert len(_peer_info(node)) == _NUM_PEERS - 2

        mocktime += 2
        node.set_mock_time(mocktime)
        wait_until(
            lambda: sum(peer.is_connected for peer in peers) == _NUM_PEERS - 3,
            timeout=_WAIT,
        )
        _serving_wait_until(peers, lambda: _is_block_requested(peers, stall_blocks[0]))
        _all_sync_send_with_ping(peers)

        # the first withheld block, once sent, brings the timeout back to two
        unexpected = [] if _relogs_stall(node) else [_STALL_STARTED]
        with _expecting(log_path, [_DECREASED], unexpected):
            for peer in peers:
                if peer.is_connected and stall_blocks[0] in peer.getdata_requests:
                    peer.send(_block_payload(block_dict[stall_blocks[0]]))
            _all_sync_send_with_ping(peers)

        # every block up to the second withheld one connects
        _serving_wait_until(
            peers, lambda: node.rpc.call("getblockcount") == _SECOND_STALL_INDEX
        )
    finally:
        for peer in peers:
            peer.peer.close()


def _manual_peer_stalling(node: NodeAdapter, log_path: Path | None) -> None:
    """Core's own `test_manual_peer_stalling`.

    :param log_path: the node's own log where the check is a log half.
    """
    initial_height = node.rpc.call("getblockcount")
    assert isinstance(initial_height, int)
    # Core's own post-cooldown block is the one after the chain
    *blocks, post_cooldown_block = _build_chain(node, _NUM_BLOCKS + 1)
    block_dict = {block.header.hash: block for block in blocks}
    stall_block = blocks[0].header.hash

    headers_message = Headers([block.header for block in blocks])

    mocktime = int(time.time()) + 1
    node.set_mock_time(mocktime)

    all_peers: list[_Staller] = []
    try:
        # a manual peer stalling on the first block
        manual_peer = _add_outbound(node, "manual", block_dict, [stall_block])
        all_peers.append(manual_peer)
        assert _peer_info(node)[0]["connection_type"] == "manual"

        # the headers go to the manual peer first, so it is asked for block 0
        manual_peer.send_and_ping(headers_message)

        # outbound peers serving every block
        outbound_peers = []
        for _ in range(_NUM_OUTBOUND_PEERS):
            peer = _add_outbound(node, "outbound-full-relay", block_dict, [])
            all_peers.append(peer)
            peer.send_and_ping(headers_message)
            outbound_peers.append(peer)

        # only the withheld block is left in flight, from the manual peer
        _serving_wait_until(all_peers, lambda: _inflight(node) == 1)
        _all_sync_send_with_ping(all_peers)
        assert manual_peer.getdata_requests.count(stall_block) == 1
        assert not _is_block_requested(outbound_peers, stall_block)

        # past the stalling timeout the manual peer is paused
        with _expecting(log_path, [_PAUSING]):
            mocktime += 3
            node.set_mock_time(mocktime)
            manual_peer.sync_with_ping()

        assert manual_peer.is_connected
        assert len(_peer_info(node)) == len(all_peers)
        assert manual_peer.getdata_requests.count(stall_block) == 1
        assert _inflight(node, "manual") == 0

        # the released block goes to another peer
        _all_sync_send_with_ping(outbound_peers)
        _serving_wait_until(
            all_peers, lambda: _is_block_requested(outbound_peers, stall_block)
        )

        # the chain connects while the manual peer is paused
        _serving_wait_until(
            all_peers,
            lambda: node.rpc.call("getblockcount") == _NUM_BLOCKS + initial_height,
        )

        # the manual peer is asked for no block during the cooldown
        block_dict[post_cooldown_block.header.hash] = post_cooldown_block
        manual_peer.send_and_ping(Headers([post_cooldown_block.header]))
        assert post_cooldown_block.header.hash not in manual_peer.getdata_requests

        # and is asked again once the cooldown has passed
        mocktime += _BLOCK_DOWNLOAD_COOLDOWN + 1
        node.set_mock_time(mocktime)
        manual_peer.sync_with_ping()
        _serving_wait_until(
            all_peers,
            lambda: post_cooldown_block.header.hash in manual_peer.getdata_requests,
        )
        _serving_wait_until(
            all_peers,
            lambda: node.rpc.call("getblockcount") == _NUM_BLOCKS + initial_height + 1,
        )
    finally:
        for peer in all_peers:
            peer.peer.close()


def _manual_is_refused(node: NodeAdapter) -> None:
    """Assert the refusal a build without a `manual` `addconnection` answers."""
    with Listener(_MAGIC) as listener, pytest.raises(RpcError) as refusal:
        node.add_outbound_connection(listener.address, "manual")
    assert refusal.value.code == _RPC_INVALID_PARAMETER


def stalling_drops_the_staller(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the wire half: each staller is dropped, the timeout doubling.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _node(cluster, skip_counts, log=False)
    _stalling(node, None)


def stalling_is_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half: the stall is logged, and the timeout decreased.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, log_path = _node(cluster, skip_counts, log=True)
    _stalling(node, log_path)


def manual_peer_stalling_pauses_the_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half: a stalling manual peer is paused, not dropped.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _node(cluster, skip_counts, log=False)
    if takes_manual(node):
        _manual_peer_stalling(node, None)
    else:
        _manual_is_refused(node)


def manual_peer_stalling_is_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half: the manual peer's pause is logged.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, log_path = _node(cluster, skip_counts, log=True)
    if takes_manual(node):
        _manual_peer_stalling(node, log_path)
    else:
        _manual_is_refused(node)
