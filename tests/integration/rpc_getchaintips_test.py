# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_getchaintips`, one body over each node.

Read from Core's `test/functional/rpc_getchaintips.py` (`fa16bc53d79c`,
2026-04-16), a file needing no mechanism the adapter lacks
([ISS 317](https://github.com/btclib-org/functional-tests/issues/317)):
four nodes linked in a line, as Core's `setup_network` links them
(`Capability.CONNECT`), the first reporting its active tip alone
(`Capability.CHAIN_TIPS`). The network is
split between the second and the third node (`Capability.DISCONNECT`),
and each half mines a chain of its own (`Capability.GENERATE`), the
second half's longer. Each half
reports its own tip alone; once joined, the first node reports the long
tip as active and the short one as a `valid-fork` branching off it.
Isolated again, the first node is given the header of a block whose
coinbase pays too much and the header of a child of it: that chain is
its `headers-only` tip, and the block itself, once submitted, turns it
`invalid`.

What differs from Core's file:

- Core's nodes start on a cached chain two hundred blocks high; here
  the first node mines those blocks, paying Core's own `PRIV_KEYS[0]`
  address, before the test's first check;
- Core's `generate` has each node pay its own `PRIV_KEYS` address, and
  so does this body, through `generatetoaddress`, so that the two halves
  mine different blocks;
- Core's `disconnect_nodes` asks the node that was dialled to drop the
  node that dialled it, and `node.disconnect_nodes` asks the dialler;
  this body then waits for the other side too, as Core's does;
- each block is built with btclib in the shape of Core's own
  `create_block` and `create_coinbase` (`blocktools.py`), its coinbase
  paying `OP_TRUE`.

Core's file at `v32.1` is the pin's. Every step is the same at `v30.3`:
Core's file there differs from the pinned revision only in how it hands
`create_block` a coinbase and a time.

Each `rpc_getchaintips_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from btclib.block.block import Block
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS, subsidy

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import (
    connect_nodes,
    disconnect_nodes,
    sync_all,
    wait_until,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["chain_tips_are_reported"]

type _Node = NodeAdapter

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

# Core's own `PRIV_KEYS` addresses (`test_framework/test_node.py`), the
# one its `generate` has each node pay, by the node's own index
_ADDRESSES = (
    "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z",
    "msX6jQXvxiNhx3Q62PKeLPrhrqZQdSimTg",
    "mnonCMyH9TmAsSj3M59DsbH8H63U3RKoFP",
    "mqJupas8Dt2uestQDvV2NH3RU8uZh2dqQR",
)

# the height of the cached chain Core's `setup_nodes` starts every node on
_CACHE_HEIGHT = 200

# how many blocks each half of the split network mines, Core's own
_SHORT_BLOCKS = 10
_LONG_BLOCKS = 20

# what Core's own `create_coinbase` pays
_OP_TRUE = b"\x51"

# Core's own `create_coinbase(..., nValue=100)`: a hundred coins, with
# no halving applied, more than any regtest block may claim
_COIN = 100_000_000
_INVALID_COINBASE_VALUE = 100 * _COIN

# Core's own `create_block` default header version,
# `VERSIONBITS_LAST_OLD_BLOCK_VERSION` (`blocktools.py`)
_BLOCK_VERSION = 4

# Core's own `sync_blocks` default timeout
_SYNC_TIMEOUT = 60.0


def _block(previous: bytes, height: int, block_time: int, fees: int = 0) -> Block:
    """Core's own `create_block(previous, create_coinbase(height))`, solved.

    :param fees: paid by the coinbase over the subsidy at `height`.
    """
    coinbase = build_coinbase(
        height, _OP_TRUE, fees=fees, halving_interval=_HALVING_INTERVAL
    )
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


def _chain_tips(node: NodeAdapter) -> list[dict[str, object]]:
    """Return `getchaintips`'s own answer."""
    tips = node.rpc.call("getchaintips")
    assert isinstance(tips, list)
    return tips


def _generate(node: NodeAdapter, index: int, count: int) -> None:
    """Core's own `generate(node, count)`: pay the node's own address."""
    node.rpc.call("generatetoaddress", [count, _ADDRESSES[index]])


def _peer_count(node: NodeAdapter) -> int:
    """Return how many peers `getpeerinfo` lists."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return len(peers)


def _disconnect(dialler: NodeAdapter, dialled: NodeAdapter) -> None:
    """Core's own `disconnect_nodes`, waiting on both sides of the link."""
    peers = _peer_count(dialled)
    disconnect_nodes(dialler, dialled)
    wait_until(lambda: _peer_count(dialled) == peers - 1)


def _assert_one_active_tip(node: NodeAdapter, height: int) -> dict[str, object]:
    """Check `node` reports one active tip at `height`, and return it."""
    tips = _chain_tips(node)
    assert len(tips) == 1
    (tip,) = tips
    assert tip["branchlen"] == 0
    assert tip["height"] == height
    assert tip["status"] == "active"
    return tip


def chain_tips_are_reported(
    cluster: Callable[[int], Sequence[_Node]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `setup_network` and `run_test`, in Core's own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes = cluster(4)
    node0, node1, node2, node3 = nodes
    for node in (node1, node2, node3):
        require(Capability.CONNECT, node.capabilities, skip_counts)
    require(Capability.GENERATE, node0.capabilities, skip_counts)
    require(Capability.GENERATE, node2.capabilities, skip_counts)
    require(Capability.DISCONNECT, node2.capabilities, skip_counts)
    require(Capability.DISCONNECT, node1.capabilities, skip_counts)
    for node in (node0, node1, node3):
        require(Capability.CHAIN_TIPS, node.capabilities, skip_counts)

    # Core's own `setup_network`: each node dials the one before it, on
    # Core's cached chain
    for dialler, dialled in zip(nodes[1:], nodes[:-1], strict=True):
        connect_nodes(dialler, dialled)
    _generate(node0, 0, _CACHE_HEIGHT)
    sync_all(nodes, timeout=_SYNC_TIMEOUT)

    # two chains of different lengths
    _assert_one_active_tip(node0, _CACHE_HEIGHT)

    # Core's own `split_network`
    _disconnect(node2, node1)
    sync_all(nodes[:2], timeout=_SYNC_TIMEOUT)
    sync_all(nodes[2:], timeout=_SYNC_TIMEOUT)
    _generate(node0, 0, _SHORT_BLOCKS)
    sync_all(nodes[:2], timeout=_SYNC_TIMEOUT)
    _generate(node2, 2, _LONG_BLOCKS)
    sync_all(nodes[2:], timeout=_SYNC_TIMEOUT)

    short_tip = _assert_one_active_tip(node1, _CACHE_HEIGHT + _SHORT_BLOCKS)
    long_tip = _assert_one_active_tip(node3, _CACHE_HEIGHT + _LONG_BLOCKS)

    # Core's own `join_network`: two tips, at least on the short half
    connect_nodes(node1, node2)
    sync_all(nodes, timeout=_SYNC_TIMEOUT)

    tips = _chain_tips(node0)
    assert len(tips) == 2
    assert tips[0] == long_tip
    assert tips[1]["branchlen"] == _SHORT_BLOCKS
    assert tips[1]["status"] == "valid-fork"
    assert {**tips[1], "branchlen": 0, "status": "active"} == short_tip

    # invalid blocks, on the first node alone
    _disconnect(node1, node0)
    best = node0.rpc.call("getbestblockhash")
    assert isinstance(best, str)
    header = node0.rpc.call("getblockheader", [best])
    assert isinstance(header, dict)
    start_height = header["height"]
    block_time = header["time"] + 1
    assert isinstance(start_height, int)
    assert isinstance(block_time, int)

    # a coinbase paying too much, and a child of it
    too_much = _INVALID_COINBASE_VALUE - subsidy(start_height + 1, _HALVING_INTERVAL)
    invalid_block = _block(bytes.fromhex(best), start_height + 1, block_time, too_much)
    block_time += 1
    block2 = _block(invalid_block.header.hash, 2, block_time)

    # a headers-only chain
    for block in (invalid_block, block2):
        node0.rpc.call(
            "submitheader", [block.header.serialize(check_validity=False).hex()]
        )
    tips = _chain_tips(node0)
    assert len(tips) == 3
    assert tips[0]["status"] == "headers-only"

    # the invalid block invalidates the headers-only chain
    node0.rpc.call("submitblock", [invalid_block.serialize(check_validity=False).hex()])
    tips = _chain_tips(node0)
    assert len(tips) == 3
    assert tips[0]["status"] == "invalid"
