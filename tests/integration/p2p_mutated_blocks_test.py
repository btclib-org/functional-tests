# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_mutated_blocks`, as bodies over each node.

Read from Core's `test/functional/p2p_mutated_blocks.py` (`9c5dd2926aa9`,
2026-06-18): a peer sending a mutated block, one whose transactions no
longer match its header's merkle root, is dropped without clearing the
request the node has in flight for that block from an honest peer.

Each of Core's checks is a wire half and a log half, each body over a
fresh node:

- an outbound full-relay peer (`Capability.TYPED_OUTBOUND`) announces a
  block by its header, then by a `cmpctblock` whose transaction beside
  the coinbase the node asks for with `getblocktxn`; an inbound peer then
  sends the block with that transaction's version changed. The node drops
  the second peer, keeps the block in flight from the first, and takes
  the block once the first sends the `blocktxn`. The log half asserts
  the line Core's own asserts,
  `Block mutated: bad-txnmrklroot, hashMerkleRoot mismatch`, and besides
  it bitcoind's own `Received mutated block from peer=1`
  and `Misbehaving: peer=1: mutated block`, which name the peer dropped.
- a peer sending that block with its parent's hash replaced by one the
  node does not know is dropped, and the log half asserts bitcoind's own
  `AcceptBlock FAILED (prev-blk-not-found)`.

The header ahead of the `cmpctblock`, and the peer's own `sendcmpct`
ahead of both, are steps of Core's file from `v32.0` and not of
`v30.3`'s. From `v32.0` a node ignores a `cmpctblock` it did not ask for
from a peer it has not chosen for high-bandwidth relay
(bitcoin/bitcoin@831359171582fc8220f9082049fddc0cc185f93d), and one from
a peer that never sent `sendcmpct`
(bitcoin/bitcoin@9c5dd2926aa97ba3d602185d5409e99f230acac4). A release
before `v32.0` passes with both steps, as `node-integration.yml`'s
`older-core` job measures.

The block spends a `MiniWallet` coin of the node under test, mined over
`Capability.MINE`, and is built and solved here rather than read from
`getblocktemplate`. BIP152's messages are btclib's own
`btclib.p2p.compact_blocks`, the `cmpctblock` built as
`p2p_compactblocks_blocksonly_test.py` builds one.

Core starts its node with `-testactivationheight=segwit@1`, so that a
block whose parent the node does not know is one segwit is not deployed
for, and the missing-parent block, which carries a witness, would read
as mutated (`unexpected-witness`) to a node checking it before looking
its parent up. Only the missing-parent log half asks for it
(`Capability.TEST_ACTIVATION_HEIGHT`): that check's peer is dropped
either way, and the mutated block extends a tip segwit is active on with
or without it. Each disconnect is awaited over `Peer`'s own default wait
rather than Core's five seconds, and the node's `getdata` and
`getblocktxn` over Core's own.

Each `p2p_mutated_blocks_<node>_test.py` module beside this one runs each body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import copy
from contextlib import nullcontext
from dataclasses import replace
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.block.block import Block
from btclib.block.mining import mine
from btclib.p2p import (
    BlockPayload,
    BlockTxn,
    CmpctBlock,
    GetBlockTxn,
    GetData,
    Headers,
    PrefilledTransaction,
    SendCmpct,
)
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.mini_wallet import MiniWallet, build_next_block
from btclib_functional_tests.peer import Listener, Peer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from btclib.p2p import Message

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "block_missing_its_parent_drops_the_peer",
    "block_missing_its_parent_is_logged",
    "mutated_block_is_logged",
    "mutated_block_keeps_the_honest_request",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

# the height of the block the checks announce, one past what is mined
_HEIGHT = COINBASE_MATURITY + 1

# Core's own waits for the node's `getdata` and `getblocktxn`
_GETDATA_WAIT = 30.0
_GETBLOCKTXN_WAIT = 5.0

# the parent Core's own `block_missing_prev` names, `hashPrevBlock = 123`
_UNKNOWN_PARENT = (123).to_bytes(32, byteorder="big")

# Core's own line says why the block is mutated, the `net` lines which peer
# sent it: the attacker is the node's second peer, the honest one its first
_MUTATED = (
    "Block mutated: bad-txnmrklroot, hashMerkleRoot mismatch",
    "Received mutated block from peer=1",
    "Misbehaving: peer=1: mutated block",
)
_MISSING_PARENT = "AcceptBlock FAILED (prev-blk-not-found)"


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


def _connect(node: NodeAdapter) -> Peer:
    """Core's own `add_p2p_connection`: a handshake, then a ping round trip."""
    peer = Peer(node.p2p_address, _MAGIC)
    try:
        peer.handshake()
        peer.sync_with_ping()
    except BaseException:
        peer.close()
        raise
    return peer


def _block_payload(block: Block) -> BlockPayload:
    """Core's own `msg_block(block)`, witnesses included."""
    return BlockPayload(block, include_witness=True, check_validity=False)


def _cmpctblock(block: Block) -> CmpctBlock:
    """Core's `HeaderAndShortIDs.initialize_from_block`, `use_witness=True`."""
    keyed = CmpctBlock(block.header, check_validity=False)
    coinbase, *rest = block.transactions
    return CmpctBlock(
        block.header,
        keyed.nonce,
        [keyed.short_id(tx.hash) for tx in rest],
        [PrefilledTransaction(0, coinbase)],
        check_validity=False,
    )


def _next_block(node: NodeAdapter) -> Block:
    """Mine to maturity, then build Core's block: a coinbase and a spend.

    The spend is never broadcast, so the node holds it in no mempool and
    a `cmpctblock` of this block leaves it to be asked for.
    """
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY)
    tx = wallet.create_self_transfer()
    return build_next_block(node, wallet.script_pub_key, [tx])


def _asks_for(block: Block) -> Callable[[Message], bool]:
    """Return whether a `getdata` names `block`: Core's `wait_for_getdata`."""

    def _names(message: Message) -> bool:
        items = GetData.parse(message.payload, check_validity=False).items
        return any(item.hash == block.header.hash for item in items)

    return _names


def _asks_for_the_spend(block: Block) -> Callable[[Message], bool]:
    """Return whether a `getblocktxn` asks for `block`'s spend alone."""

    def _asks(message: Message) -> bool:
        request = GetBlockTxn.parse(message.payload, check_validity=False)
        return request.block_hash == block.header.hash and request.indexes == (1,)

    return _asks


def _assert_in_flight_from_the_honest_peer(node: NodeAdapter) -> None:
    """Core's own: peer 0, the honest one, has the block alone in flight."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    assert peers[0]["id"] == 0
    assert peers[0]["inflight"] == [_HEIGHT]


def _attack(node: NodeAdapter, log_path: Path | None) -> tuple[Peer, Block]:
    """Core's own steps up to the attacker's drop; return the honest peer.

    :param log_path: the node's own log where the check is a log half, the
        mutated block's wording then asserted as the attacker sends it.
    :returns: the honest peer, still connected, and the block it announced.
    """
    block = _next_block(node)
    # the spend's own version changed, the header's merkle root left alone
    mutated_spend = copy.deepcopy(block.transactions[1])
    mutated_spend.version = 4
    mutated = Block(
        block.header, [block.transactions[0], mutated_spend], check_validity=False
    )

    with Listener(_MAGIC) as listener:
        node.add_outbound_connection(listener.address, "outbound-full-relay")
        honest = listener.accept()
    try:
        honest.handshake()
        honest.sync_with_ping()
        honest.send(SendCmpct())
        honest.sync_with_ping()
        with _connect(node) as attacker:
            honest.send(Headers([block.header], check_validity=False))
            honest.wait_for(
                "getdata", predicate=_asks_for(block), timeout=_GETDATA_WAIT
            )
            honest.send(_cmpctblock(block), check_validity=False)
            honest.wait_for(
                "getblocktxn",
                predicate=_asks_for_the_spend(block),
                timeout=_GETBLOCKTXN_WAIT,
            )
            _assert_in_flight_from_the_honest_peer(node)

            with _expecting(log_path, _MUTATED):
                attacker.send(_block_payload(mutated), check_validity=False)
                attacker.wait_for_disconnect()
    except BaseException:
        honest.close()
        raise
    return honest, block


def mutated_block_keeps_the_honest_request(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half: the mutated block's sender alone is dropped.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    honest, block = _attack(node, None)
    with honest:
        _assert_in_flight_from_the_honest_peer(node)
        honest.send(BlockTxn(block.header.hash, block.transactions[1:]))
        honest.sync_with_ping()
    assert node.rpc.call("getbestblockhash") == block.header.hash.hex()


def mutated_block_is_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half: bitcoind's own wording for the mutated block.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    honest, _ = _attack(node, _debug_log(node, skip_counts))
    honest.close()


def _send_missing_its_parent(node: NodeAdapter, log_path: Path | None) -> None:
    """Core's own: the block, its parent's hash unknown, from a fresh peer.

    :param log_path: the node's own log where the check is a log half.
    """
    block = _next_block(node)
    header = mine(replace(block.header, previous_block_hash=_UNKNOWN_PARENT))
    assert header is not None
    missing_parent = Block(header, block.transactions, check_validity=False)
    with _connect(node) as attacker:
        assert len(node.rpc.call("getpeerinfo")) == 1
        with _expecting(log_path, [_MISSING_PARENT]):
            attacker.send(_block_payload(missing_parent), check_validity=False)
            attacker.wait_for_disconnect()


def block_missing_its_parent_drops_the_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half: a block whose parent is unknown drops its sender.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.MINE, node.capabilities, skip_counts)
    _send_missing_its_parent(node, None)


def block_missing_its_parent_is_logged(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the log half: the missing parent, not a mutation, is logged.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.MINE, node.capabilities, skip_counts)
    require(Capability.TEST_ACTIVATION_HEIGHT, node.capabilities, skip_counts)
    log_path = _debug_log(node, skip_counts)
    node.restart(["-testactivationheight=segwit@1"])
    _send_missing_its_parent(node, log_path)
