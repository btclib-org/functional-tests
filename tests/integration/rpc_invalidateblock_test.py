# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_invalidateblock`, one body over each node.

Read from Core's `test/functional/rpc_invalidateblock.py`
(`fab352053d6e`, 2026-04-16), a file needing no mechanism the adapter
lacks ([ISS 317](https://github.com/btclib-org/functional-tests/issues/317)):
`invalidateblock` and `reconsiderblock` (`Capability.INVALIDATE_BLOCK`)
mark a block invalid and take the mark back off. The nodes start
unlinked on a clean chain, and the first and the second node each mine a
chain of their own (`Capability.GENERATE`), the second node's longer.
Linked (`Capability.CONNECT`), the first node reorgs to the second
node's chain and is given the header of a block on top of it.
Invalidating a block of that chain takes the first node back to its own,
with no header left beyond its tip; reconsidering that chain's tip
counts the header beyond it again, and invalidating the block once more
takes the first node back. With the second and the third node linked,
each invalidates a block of the shared chain, and the third mines a
block on what is left: no node reorgs to a chain of less work. With the
first node's own chain invalidated and the other chain reconsidered, an
ancestor of that chain's tip is invalidated; reconsidering the header
then makes the header's last ancestor holding its block data the tip,
the header counted beyond it. Reconsidering a block reconsiders its
invalidated ancestors, and reconsidering an ancestor reconsiders its
invalidated descendants. An unknown block is refused as not found.

What differs from Core's file:

- Core's `generate` has each node pay its own `PRIV_KEYS` address, and
  so does this body, through `generatetoaddress`, so that the first and
  the second node mine different chains;
- Core's `generatetodescriptor` pays `ADDRESS_BCRT1_UNSPENDABLE_DESCRIPTOR`
  (`test_framework/address.py`), an `addr()` descriptor; this body pays
  the address it wraps, through `generatetoaddress`;
- the header is built with btclib in the shape of Core's own
  `create_block` (`blocktools.py`), its coinbase paying `OP_TRUE`;
- the check that a reconsidered block's ancestors become tip candidates
  runs on every node but a bitcoind whose `getnetworkinfo` `version` is
  below `v30.0`, the first release carrying Core's change and Core's own
  file gaining the check with it. A release before it, `v29.4` among them,
  keeps its tip below the header's ancestors and later aborts on its own
  `CheckBlockIndex`; Core's own file at `v29.4` leaves the check out, as
  this body does there. The threshold leaves the check out where it
  could run only for a `master` build reporting `29.99` taken after the
  change's merge,
  bitcoin/bitcoin@a40e9536588c366886de4f4b9d67b8665a509929.

Core's file at `v32.1` is the pin's. Every step is the same at `v30.3`:
Core's file there differs from the pinned revision only in how it hands
`create_block` a coinbase and a time.

Each `rpc_invalidateblock_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError, RPCErrorCode
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import (
    connect_nodes,
    wait_until,
    wait_until_tips_agree,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["invalidateblock_and_reconsiderblock_move_the_tip"]

type _Node = NodeAdapter

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

# Core's own `PRIV_KEYS` addresses (`test_framework/test_node.py`), the
# one its `generate` has each node pay, by the node's own index
_ADDRESSES = (
    "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z",
    "msX6jQXvxiNhx3Q62PKeLPrhrqZQdSimTg",
    "mnonCMyH9TmAsSj3M59DsbH8H63U3RKoFP",
)

# the address Core's `ADDRESS_BCRT1_UNSPENDABLE_DESCRIPTOR` wraps
_UNSPENDABLE_ADDRESS = (
    "bcrt1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3xueyj"
)

# what Core's own `create_coinbase` pays
_OP_TRUE = b"\x51"

# Core's own `create_block(..., version=4)`
_BLOCK_VERSION = 4

# Core's own `sync_blocks` default timeout
_SYNC_TIMEOUT = 60.0

# Core's own `wait_until(..., timeout=5)` on each node's height
_HEIGHT_TIMEOUT = 5.0

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v30.0`, the first
# release in which a reconsidered block's ancestors become tip candidates
# (bitcoin/bitcoin#30479)
_ANCESTORS_RECONSIDERED_VERSION = 300000


def _header(previous: str, height: int, block_time: int) -> tuple[str, str]:
    """Core's own `create_block(previous, height=height, ...)`, solved.

    :return: the serialized header, and its hash.
    """
    coinbase = build_coinbase(height, _OP_TRUE, halving_interval=_HALVING_INTERVAL)
    candidate = build_block(
        bytes.fromhex(previous),
        [coinbase],
        datetime.fromtimestamp(block_time, UTC),
        REGTEST_POW_LIMIT_BITS,
        version=_BLOCK_VERSION,
    )
    solved = mine(candidate.header)
    assert solved is not None
    return solved.serialize(check_validity=False).hex(), solved.hash.hex()


def _generate(node: NodeAdapter, address: str, count: int) -> list[str]:
    """Core's own `generate`: mine `count` blocks paying `address`.

    :return: the hashes of the blocks mined, oldest first.
    """
    hashes = node.rpc.call("generatetoaddress", [count, address])
    assert isinstance(hashes, list)
    return hashes


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


def _hash_at(node: NodeAdapter, height: int) -> str:
    """Return `getblockhash`'s own answer at `height`."""
    block_hash = node.rpc.call("getblockhash", [height])
    assert isinstance(block_hash, str)
    return block_hash


def _heights(node: NodeAdapter) -> tuple[int, int]:
    """Return `getblockchaininfo`'s own `blocks` and `headers`."""
    info = node.rpc.call("getblockchaininfo")
    assert isinstance(info, dict)
    return info["blocks"], info["headers"]


def _reconsiders_ancestors(node: NodeAdapter) -> bool:
    """Whether a block `node` reconsiders makes its ancestors tip candidates.

    Core's own claim for every node, bitcoind before
    `_ANCESTORS_RECONSIDERED_VERSION` excepted, read off its own
    `getnetworkinfo` `version`
    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
    """
    if not isinstance(node, BitcoindAdapter):
        return True
    info = node.rpc.call("getnetworkinfo")
    assert isinstance(info, dict)
    version = info["version"]
    assert isinstance(version, int)
    return version >= _ANCESTORS_RECONSIDERED_VERSION


def _invalidate(node: NodeAdapter, block_hash: str) -> None:
    """Ask `node` to mark `block_hash` invalid."""
    node.rpc.call("invalidateblock", [block_hash])


def _reconsider(node: NodeAdapter, block_hash: str) -> None:
    """Ask `node` to take the invalid mark off `block_hash`."""
    node.rpc.call("reconsiderblock", [block_hash])


def _ancestors_become_tip_candidates(
    node0: NodeAdapter, tip: str, header_hash: str
) -> None:
    """Core's own check that a reconsidered block's ancestors can be the tip.

    :param node0: the first node, on its own chain.
    :param tip: the tip of the chain the header is on.
    :param header_hash: the header's own hash, its block never submitted.
    """
    # the first node's own chain is invalidated so that it is not reorged
    # back to, and the header's chain is taken back
    _invalidate(node0, _hash_at(node0, 1))
    _reconsider(node0, tip)
    blockhash_3 = _hash_at(node0, 3)
    blockhash_4 = _hash_at(node0, 4)
    blockhash_6 = _hash_at(node0, 6)
    assert _best(node0) == blockhash_6

    _invalidate(node0, blockhash_4)
    assert _best(node0) == blockhash_3
    assert _heights(node0) == (3, 3)

    # a header with no block data cannot be the tip, its ancestor can
    _reconsider(node0, header_hash)
    assert _best(node0) == blockhash_6
    assert _heights(node0) == (6, 7)


def invalidateblock_and_reconsiderblock_move_the_tip(
    cluster: Callable[[int], Sequence[_Node]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `run_test`, in Core's own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes = cluster(3)
    node0, node1, node2 = nodes
    for node in nodes:
        require(Capability.GENERATE, node.capabilities, skip_counts)
    require(Capability.CONNECT, node0.capabilities, skip_counts)
    require(Capability.CONNECT, node1.capabilities, skip_counts)
    for node in nodes:
        require(Capability.INVALIDATE_BLOCK, node.capabilities, skip_counts)

    # setBlockIndexCandidates is repopulated after InvalidateBlock
    _generate(node0, _ADDRESSES[0], 4)
    assert _count(node0) == 4
    besthash_n0 = _best(node0)

    _generate(node1, _ADDRESSES[1], 6)
    assert _count(node1) == 6

    # linked, the first node reorgs to the second node's chain
    connect_nodes(node0, node1)
    wait_until_tips_agree(nodes[:2], timeout=_SYNC_TIMEOUT)
    assert _count(node0) == 6

    # a header on the first node's tip, its block never submitted; Core's
    # own `create_block` is handed the tip's height rather than the block's
    tip = _best(node0)
    tip_block = node0.rpc.call("getblock", [tip])
    assert isinstance(tip_block, dict)
    header, header_hash = _header(tip, _count(node0), tip_block["time"] + 1)
    node0.rpc.call("submitheader", [header])
    blocks, headers = _heights(node0)
    assert headers == blocks + 1

    # invalidating the second block takes the first node back to its own chain
    badhash = _hash_at(node1, 2)
    _invalidate(node0, badhash)
    assert _count(node0) == 4
    assert _best(node0) == besthash_n0
    blocks, headers = _heights(node0)
    assert headers == blocks

    # reconsidering the sixth block sets the best header again
    _reconsider(node0, tip)
    blocks, headers = _heights(node0)
    assert headers == blocks + 1

    _invalidate(node0, badhash)
    assert _count(node0) == 4
    assert _best(node0) == besthash_n0
    blocks, headers = _heights(node0)
    assert headers == blocks

    # no reorg to a chain of less work
    connect_nodes(node1, node2)
    wait_until_tips_agree(nodes[1:], timeout=_SYNC_TIMEOUT)
    assert _count(node2) == 6
    _invalidate(node1, _hash_at(node1, 5))
    assert _count(node1) == 4
    _invalidate(node2, _hash_at(node2, 3))
    assert _count(node2) == 2
    _generate(node2, _ADDRESSES[2], 1)
    wait_until(lambda: _count(node2) == 3, timeout=_HEIGHT_TIMEOUT)
    wait_until(lambda: _count(node0) == 4, timeout=_HEIGHT_TIMEOUT)
    wait_until(lambda: _count(node1) == 4, timeout=_HEIGHT_TIMEOUT)

    if _reconsiders_ancestors(node0):
        _ancestors_become_tip_candidates(node0, tip, header_hash)

    # every ancestor is reconsidered too
    mined = _generate(node1, _UNSPENDABLE_ADDRESS, 10)
    assert _best(node1) == mined[-1]
    _invalidate(node1, mined[-1])
    _invalidate(node1, mined[-2])
    assert _best(node1) == mined[-3]
    _reconsider(node1, mined[-1])
    assert _best(node1) == mined[-1]

    # every descendant is reconsidered too
    mined = _generate(node1, _UNSPENDABLE_ADDRESS, 10)
    assert _best(node1) == mined[-1]
    _invalidate(node1, mined[-2])
    _invalidate(node1, mined[-4])
    assert _best(node1) == mined[-5]
    _reconsider(node1, mined[-4])
    assert _best(node1) == mined[-1]
    blocks, headers = _heights(node1)
    assert headers == blocks

    # an unknown block is refused
    with pytest.raises(RpcError, match="Block not found") as refused:
        _invalidate(node1, "00" * 32)
    assert refused.value.code == RPCErrorCode.INVALID_ADDRESS_OR_KEY
    assert _best(node1) == mined[-1]
