# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_chain_tiebreaks`, one body over each node.

Read from Core's `test/functional/feature_chain_tiebreaks.py`
(`20ae9b98eab2`, 2026-03-04), a file needing no mechanism the adapter
lacks ([ISS 317](https://github.com/btclib-org/functional-tests/issues/317)):
of the blocks of equal work a node holds, it takes as its tip the one
whose chain it held in full first, and keeps that tip across a restart.
The nodes start unlinked on a clean chain, each fed blocks by a peer.

In Core's own labels, the first node takes `B0` on genesis, then the
headers of `B1` and `B2` on it over `submitheader`, and `B2`'s block
before `B1`'s: `B2` is the tip. Given the headers of `B3` to `B6`, on
`B1` and `B2`, and of `B7` to `B10`, on `B3` and `B4`, the tip stays;
given the blocks of `B7` to `B10`, whose parents' blocks it lacks, it
stays too. Given `B4`'s block and then `B3`'s, the node takes `B9`
rather than `B7`, which it received first: `B9`'s chain was complete
before `B7`'s, and `B9` arrived before `B10`, its sibling. Invalidating
`B9` and `B10` (`Capability.INVALIDATE_BLOCK`) takes the node to `B7`.

The second node mines a block (`Capability.GENERATE`) and is handed
`A1`, `A2` and `A3` on it, of equal work: `A1` is the tip. Restarted,
the node keeps `A1` as its tip, and takes a block built on it.

What differs from Core's file:

- each block is built with btclib in the shape of Core's own
  `create_block` (`blocktools.py`), its coinbase paying `OP_TRUE`;
- Core's `P2PDataStore` answers every `getdata` naming a block it
  holds; here the peer waits for the `getdata` naming the block it
  announced and sends that block, which is what `send_blocks_and_test`
  waits for before its own `ping` round trip;
- the check that the node keeps its tip across the restart runs where
  the running build's `getnetworkinfo` `version` is `v31.0` or later,
  the first release carrying Core's change (bitcoin/bitcoin#29640) and
  the first carrying Core's file. A release before it, `v29.4` and
  `v30.3` among them, breaks a tie between blocks it loaded from disk
  on the address each sits at in memory, as its own
  `CBlockIndexWorkComparator` (`src/node/blockstorage.cpp`) says, so a
  restart can leave `A2` or `A3` its tip. The threshold leaves the
  check out for a `master` build reporting `30.99` taken after the
  change's merge, bitcoin/bitcoin@56e9703968e26353fd4663e07a7bba198a272d12.

Every step is the same at `v32.1`, whose copy of the file is the pin's.

Each `feature_chain_tiebreaks_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.block.block import Block
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS
from btclib.p2p import BlockPayload, GetData, Headers

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.p2p import Message

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["equal_work_tips_are_broken_by_arrival"]

type _Node = NodeAdapter

_MAGIC = magic_from_chain("regtest")

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

# Core's own `PRIV_KEYS[1]` address (`test_framework/test_node.py`), the
# one its `generate` has the second node pay
_ADDRESS = "msX6jQXvxiNhx3Q62PKeLPrhrqZQdSimTg"

# what Core's own `create_coinbase` pays
_OP_TRUE = b"\x51"

# Core's own `create_block` default version, its
# `VERSIONBITS_LAST_OLD_BLOCK_VERSION`
_BLOCK_VERSION = 4

# Core's own `create_block` default: the wall clock ten minutes ahead
_BLOCK_TIME_AHEAD = 600

# how many blocks Core builds on the first node's first block: two on
# it, and two on each of those twice over
_TREE_BLOCKS = 10

# Core's own `send_blocks_and_test` default wait
_WAIT = 60.0

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v31.0`, the first
# release keeping a tip tied with another across a restart
# (bitcoin/bitcoin#29640)
_TIP_KEPT_VERSION = 310000


def _block(previous: bytes, height: int, block_time: int) -> Block:
    """Core's own `create_block(previous, tmpl={"height": ...})`, solved."""
    coinbase = build_coinbase(height, _OP_TRUE, halving_interval=_HALVING_INTERVAL)
    candidate = build_block(
        previous,
        [coinbase],
        datetime.fromtimestamp(block_time, UTC),
        REGTEST_POW_LIMIT_BITS,
        version=_BLOCK_VERSION,
    )
    solved = mine(candidate.header)
    assert solved is not None
    return Block(solved, candidate.transactions, check_validity=False)


def _time(block: Block) -> int:
    """Return `block`'s own time, in seconds."""
    return int(block.header.time.timestamp())


def _connected_peer(node: NodeAdapter) -> Peer:
    """Core's own `add_p2p_connection`: a handshake, then a ping round trip."""
    peer = Peer(node.p2p_address, _MAGIC)
    try:
        peer.handshake()
        peer.sync_with_ping()
    except BaseException:
        peer.close()
        raise
    return peer


def _asks_for(block_hash: bytes) -> Callable[[Message], bool]:
    """Return whether a `getdata` names `block_hash`."""

    def predicate(message: Message) -> bool:
        items = GetData.parse(message.payload, check_validity=False).items
        return any(item.hash == block_hash for item in items)

    return predicate


def _best(node: NodeAdapter) -> str:
    """Return `getbestblockhash`'s own answer."""
    best = node.rpc.call("getbestblockhash")
    assert isinstance(best, str)
    return best


def _count(node: NodeAdapter) -> int:
    """Return `getblockcount`'s own answer."""
    count = node.rpc.call("getblockcount")
    assert isinstance(count, int)
    return count


def _send_headers(node: NodeAdapter, blocks: Sequence[Block]) -> None:
    """Core's own `send_headers`: each header over `submitheader`."""
    for block in blocks:
        header = block.header.serialize(check_validity=False).hex()
        node.rpc.call("submitheader", [header])


def _send_block_and_test(
    peer: Peer,
    node: NodeAdapter,
    block: Block,
    *,
    success: bool,
    force_send: bool = False,
) -> None:
    """Core's own `send_blocks_and_test([block], node, ...)`, one block.

    :param success: the block is the tip once sent, rather than not.
    :param force_send: the block sent unasked, Core's own `force_send`,
        rather than its header first and the block once asked for.
    """
    block_hash = block.header.hash
    payload = BlockPayload(block, include_witness=True, check_validity=False)
    if not force_send:
        peer.send(Headers([block.header], check_validity=False))
        peer.wait_for("getdata", predicate=_asks_for(block_hash), timeout=_WAIT)
    peer.send(payload, check_validity=False)
    peer.sync_with_ping(timeout=_WAIT)
    if success:
        wait_until(lambda: _best(node) == block_hash.hex(), timeout=_WAIT)
    else:
        assert _best(node) != block_hash.hex()


def _keeps_its_tip_across_a_restart(node: NodeAdapter) -> bool:
    """Whether `node` keeps a tip tied with another across a restart.

    Core's own claim for every node, bitcoind before `_TIP_KEPT_VERSION`
    excepted, read off its own `getnetworkinfo` `version`
    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
    """
    if not isinstance(node, BitcoindAdapter):
        return True
    info = node.rpc.call("getnetworkinfo")
    assert isinstance(info, dict)
    version: int = info["version"]
    return version >= _TIP_KEPT_VERSION


def _chain_split_in_memory(node: NodeAdapter) -> None:
    """Core's own `test_chain_split_in_memory`, over the first node."""
    with _connected_peer(node) as peer:
        # B0 on genesis; B1 and B2 on it; B3 to B6 on those, two each; and
        # B7 to B10 on B3 and B4, two each, B5 and B6 left without
        start_height = _count(node)
        blocks = [
            _block(
                bytes.fromhex(_best(node)),
                start_height + 1,
                int(time.time()) + _BLOCK_TIME_AHEAD,
            )
        ]
        for i in range(1, _TREE_BLOCKS + 1):
            block = _block(
                blocks[(i - 1) >> 1].header.hash,
                start_height + (i + 1).bit_length(),
                _time(blocks[-1]) + 1,
            )
            blocks.append(block)

        _send_block_and_test(peer, node, blocks[0], success=True)
        assert _best(node) == blocks[0].header.hash.hex()

        # B2's block received first, B2 is the tip
        _send_headers(node, blocks[1:3])
        _send_block_and_test(peer, node, blocks[2], success=True)
        _send_block_and_test(peer, node, blocks[1], success=False)
        assert _best(node) == blocks[2].header.hash.hex()

        # headers alone move no tip
        _send_headers(node, blocks[3:])
        assert _best(node) == blocks[2].header.hash.hex()

        # B7 to B10 lack their parents
        for block in blocks[7:]:
            _send_block_and_test(peer, node, block, success=False)
        assert _best(node) == blocks[2].header.hash.hex()

        # B4 completes B9's chain before B3 completes B7's
        _send_block_and_test(peer, node, blocks[4], success=False, force_send=True)
        _send_block_and_test(peer, node, blocks[3], success=False, force_send=True)
        assert _best(node) == blocks[9].header.hash.hex()

        node.rpc.call("invalidateblock", [blocks[9].header.hash.hex()])
        node.rpc.call("invalidateblock", [blocks[10].header.hash.hex()])
        assert _best(node) == blocks[7].header.hash.hex()

        node.rpc.call("invalidateblock", [blocks[0].header.hash.hex()])


def _chain_split_from_disk(node: NodeAdapter) -> None:
    """Core's own `test_chain_split_from_disk`, over the second node."""
    with _connected_peer(node) as peer:
        node.rpc.call("generatetoaddress", [1, _ADDRESS])

        # A1, A2 and A3, of equal work, on the tip
        start_height = _count(node)
        tip = _best(node)
        tip_block = node.rpc.call("getblock", [tip])
        assert isinstance(tip_block, dict)
        prev_time = tip_block["time"]
        blocks = [
            _block(bytes.fromhex(tip), start_height + 1, prev_time + i + 1)
            for i in range(3)
        ]

        _send_block_and_test(peer, node, blocks[0], success=True)
        _send_block_and_test(peer, node, blocks[1], success=False)
        _send_block_and_test(peer, node, blocks[2], success=False)

    node.restart()
    if _keeps_its_tip_across_a_restart(node):
        assert _best(node) == blocks[0].header.hash.hex()
    next_block = _block(blocks[0].header.hash, start_height + 2, prev_time + 10)
    with _connected_peer(node) as peer:
        _send_block_and_test(peer, node, next_block, success=True)


def equal_work_tips_are_broken_by_arrival(
    cluster: Callable[[int], Sequence[_Node]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `run_test`, in Core's own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node0, node1 = cluster(2)
    require(Capability.INVALIDATE_BLOCK, node0.capabilities, skip_counts)
    require(Capability.GENERATE, node1.capabilities, skip_counts)

    _chain_split_in_memory(node0)
    _chain_split_from_disk(node1)
