# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_unrequested_blocks`, one body over each node.

Read from Core's `test/functional/p2p_unrequested_blocks.py`
(`fab352053d6e`, 2026-04-16), an option and the log beside node-linking
([ISS 14](https://github.com/btclib-org/functional-tests/issues/14)):
two nodes not linked, the second under `-minimumchainwork=0x10`
(`Capability.MINIMUM_CHAIN_WORK`), each mining a block of its own
(`Capability.GENERATE`) and each fed blocks by a peer. The first node
takes a block extending its tip; the second stores no header for one
whose chain is below its floor, and its log says so
(`Capability.DEBUG_LOG`). The first node keeps the header alone of an
unrequested block forking from genesis, and stores without connecting
a block of that fork with as much work as its tip or more. A block
whose parent it has no header for drops the peer. Given the missing
header, it stores every block of a deep fork but the one more than
`MIN_BLOCKS_TO_KEEP` past its tip. The fork's first block, sent again
unrequested, is ignored; an `inv` for the fork's third block makes the
node ask for the first, and once sent, the node reorganises onto the
fork. A fork whose block spends an immature coinbase drops the peer,
the node staying on the chain it had with the invalid block stored; a
header extending that fork drops the next peer. The first node then
dials the second (`Capability.CONNECT`), and both reach the same tip.

What differs from Core's file:

- Core's harness starts each node under its own `extra_args`; this
  harness's nodes start without, and the second is restarted under the
  option before any peer connects;
- each block is built with btclib in the shape of Core's own
  `create_block` and `create_coinbase` (`blocktools.py`), its coinbase
  paying `OP_TRUE`, and the immature spend is Core's own
  `create_tx_with_script(..., script_sig=b"42", amount=1)`;
- Core's `getblock` of the fork's third block is asked twice in a row,
  and once here.

Core's file at `v32.1` is the pin's. Every step is the same at `v30.3`:
Core's file there differs from the pinned revision only in handing each
`create_block` a coinbase of its own `create_coinbase` rather than a
height.

Each `p2p_unrequested_blocks_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError, magic_from_chain
from btclib.block.block import Block
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS
from btclib.p2p import (
    BlockPayload,
    GetData,
    Headers,
    Inv,
    Inventory,
    InventoryType,
)
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import SEQUENCE_FINAL

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import (
    connect_nodes,
    wait_until,
    wait_until_tips_agree,
)
from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["unrequested_blocks_are_processed_only_when_useful"]

type _Node = NodeAdapter

_MAGIC = magic_from_chain("regtest")

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

# Core's own `extra_args` for the second node
_MINIMUM_CHAIN_WORK = "-minimumchainwork=0x10"

# Core's own `PRIV_KEYS[0]` and `PRIV_KEYS[1]` addresses
# (`test_framework/test_node.py`), which its `generate` has the first
# and the second node pay
_ADDRESSES = (
    "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z",
    "msX6jQXvxiNhx3Q62PKeLPrhrqZQdSimTg",
)

# how many blocks Core builds past the fork's third, Core's own
# `MIN_BLOCKS_TO_KEEP` (`src/validation.h`): a block higher than that past
# the node's tip is too far ahead to store, which the last of them is
_FORK_BLOCKS = 288

# what Core's own `create_coinbase` pays
_OP_TRUE = b"\x51"

# Core's own `script_sig` and `amount` for the immature spend
_IMMATURE_SCRIPT_SIG = b"42"
_IMMATURE_AMOUNT = 1

# `RPC_MISC_ERROR` and `RPC_INVALID_ADDRESS_OR_KEY` (`src/rpc/protocol.h`),
# with Core's own wording for each
_NOT_DOWNLOADED_CODE = -1
_NOT_DOWNLOADED = "Block not available (not fully downloaded)"
_NOT_FOUND_CODE = -5
_NOT_FOUND = "Block not found"

# Core's own `sync_blocks` default timeout
_SYNC_TIMEOUT = 60.0


def _debug_log(node: _Node) -> Path:
    """Return the log a check reads, `Capability.DEBUG_LOG`'s fact.

    :raises TypeError: `node` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return node.debug_log_path


def _block(
    previous: bytes, height: int, block_time: int, transactions: Sequence[Tx] = ()
) -> Block:
    """Core's own `create_block(previous, height=, ntime=, txlist=)`, solved."""
    coinbase = build_coinbase(height, _OP_TRUE, halving_interval=_HALVING_INTERVAL)
    candidate = build_block(
        previous,
        [coinbase, *transactions],
        datetime.fromtimestamp(block_time, UTC),
        REGTEST_POW_LIMIT_BITS,
    )
    solved = mine(candidate.header)
    assert solved is not None
    return Block(solved, candidate.transactions, check_validity=False)


def _child(parent: Block, height: int, transactions: Sequence[Tx] = ()) -> Block:
    """Return the block on `parent` at `height`, a second after it."""
    block_time = int(parent.header.time.timestamp()) + 1
    return _block(parent.header.hash, height, block_time, transactions)


def _immature_spend(block: Block) -> Tx:
    """Core's own `create_tx_with_script(block.vtx[0], 0, b"42", amount=1)`."""
    tx_in = TxIn(
        OutPoint(block.transactions[0].id, 0), _IMMATURE_SCRIPT_SIG, SEQUENCE_FINAL
    )
    return Tx(2, 0, [tx_in], [TxOut(_IMMATURE_AMOUNT, b"")], check_validity=False)


def _block_payload(block: Block) -> BlockPayload:
    """Core's own `msg_block(block)`."""
    return BlockPayload(block, include_witness=True, check_validity=False)


def _headers(*blocks: Block) -> Headers:
    """Core's own `msg_headers` over `CBlockHeader(block)` for each block."""
    return Headers([block.header for block in blocks], check_validity=False)


def _connected_peer(node: NodeAdapter) -> Peer:
    """Core's own `add_p2p_connection`: a peer past its handshake."""
    peer = Peer(node.p2p_address, _MAGIC)
    try:
        peer.handshake()
        peer.sync_with_ping()
    except BaseException:
        peer.close()
        raise
    return peer


def _send_and_ping(peer: Peer, block: Block) -> None:
    """Core's own `send_and_ping(msg_block(block))`."""
    peer.send(_block_payload(block), check_validity=False)
    peer.sync_with_ping()


def _disconnect_p2ps(node: NodeAdapter, peer: Peer) -> None:
    """Core's own `disconnect_p2ps`: close the peer, and wait for the node."""
    peer.close()
    wait_until(lambda: node.rpc.call("getpeerinfo") == [])


def _block_count(node: NodeAdapter) -> int:
    """Return `getblockcount`'s own answer."""
    count = node.rpc.call("getblockcount")
    assert isinstance(count, int)
    return count


def _best_block_hash(node: NodeAdapter) -> str:
    """Return `getbestblockhash`'s own answer."""
    block_hash = node.rpc.call("getbestblockhash")
    assert isinstance(block_hash, str)
    return block_hash


def _chain_tip_status(node: NodeAdapter, block: Block) -> str | None:
    """Return the `getchaintips` status of `block`, `None` where no tip."""
    tips = node.rpc.call("getchaintips")
    assert isinstance(tips, list)
    for tip in tips:
        if tip["hash"] == block.header.hash.hex():
            status = tip["status"]
            assert isinstance(status, str)
            return status
    return None


def _getblock(node: NodeAdapter, block: Block) -> dict[str, object]:
    """Return `getblock`'s own answer, which fails for a block not stored."""
    answer = node.rpc.call("getblock", [block.header.hash.hex()])
    assert isinstance(answer, dict)
    return answer


def _assert_rpc_error(
    node: NodeAdapter, method: str, block: Block, code: int, message: str
) -> None:
    """Core's own `assert_raises_rpc_error(code, message, method, hash)`."""
    with pytest.raises(RpcError) as refused:
        node.rpc.call(method, [block.header.hash.hex()])
    assert refused.value.code == code
    assert message in str(refused.value)


def _assert_not_downloaded(node: NodeAdapter, block: Block) -> None:
    """Core's own check that `getblock` finds the header and no block."""
    _assert_rpc_error(node, "getblock", block, _NOT_DOWNLOADED_CODE, _NOT_DOWNLOADED)


def unrequested_blocks_are_processed_only_when_useful(
    cluster: Callable[[int], Sequence[_Node]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `setup_network` and `run_test`, in Core's own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node0, node1 = cluster(2)
    require(Capability.MINIMUM_CHAIN_WORK, node1.capabilities, skip_counts)
    require(Capability.GENERATE, node0.capabilities, skip_counts)
    require(Capability.GENERATE, node1.capabilities, skip_counts)
    require(Capability.DEBUG_LOG, node1.capabilities, skip_counts)
    require(Capability.CONNECT, node0.capabilities, skip_counts)

    # Core's own `setup_network`: two nodes, not linked
    node1.restart([_MINIMUM_CHAIN_WORK])
    test_node = _connected_peer(node0)
    min_work_node = _connected_peer(node1)

    # 1. a block mined by each node, out of initial block download
    for node, address in zip((node0, node1), _ADDRESSES, strict=True):
        node.rpc.call("generatetoaddress", [1, address])
    tips = [bytes.fromhex(_best_block_hash(node)) for node in (node0, node1)]

    # 2. a block on each tip: taken by the first node, and below the
    # second node's floor, its header not stored
    block_time = int(time.time()) + 1
    blocks_h2 = []
    for tip in tips:
        blocks_h2.append(_block(tip, 2, block_time))
        block_time += 1
    _send_and_ping(test_node, blocks_h2[0])
    not_adding = (
        f"AcceptBlockHeader: not adding new block header "
        f"{blocks_h2[1].header.hash.hex()}, "
        "missing anti-dos proof-of-work validation"
    )
    with assert_debug_log(_debug_log(node1), [not_adding]):
        _send_and_ping(min_work_node, blocks_h2[1])
    assert _block_count(node0) == 2
    assert _block_count(node1) == 1
    assert _chain_tip_status(node1, blocks_h2[1]) is None

    # 3. a block forking from genesis, unrequested: its header alone
    genesis = bytes.fromhex(str(node0.rpc.call("getblockhash", [0])))
    block_h1f = _block(genesis, 1, block_time)
    block_time += 1
    _send_and_ping(test_node, block_h1f)
    assert _chain_tip_status(node0, block_h1f) == "headers-only"
    _assert_not_downloaded(node0, block_h1f)

    # 4. a second block on the fork, as much work as the tip: stored,
    # its parent still missing
    block_h2f = _block(block_h1f.header.hash, 2, block_time)
    _send_and_ping(test_node, block_h2f)
    assert _chain_tip_status(node0, block_h2f) == "headers-only"
    _getblock(node0, block_h2f)

    # 4b. a third, more work than the tip: stored, not connected
    block_h3 = _child(block_h2f, 3)
    _send_and_ping(test_node, block_h3)
    assert _chain_tip_status(node0, block_h3) == "headers-only"
    _getblock(node0, block_h3)

    # 4c. the deep fork, one block more than the node stores ahead
    all_blocks = []
    tip_block = block_h3
    for height in range(4, 4 + _FORK_BLOCKS):
        tip_block = _child(tip_block, height)
        all_blocks.append(tip_block)

    # a block whose parent the node has no header for: the peer dropped
    test_node.send(_block_payload(all_blocks[1]), check_validity=False)
    test_node.wait_for_disconnect()
    test_node.close()
    for method in ("getblock", "getblockheader"):
        _assert_rpc_error(node0, method, all_blocks[1], _NOT_FOUND_CODE, _NOT_FOUND)
    test_node = _connected_peer(node0)

    # the same block taken once the missing header is sent first
    test_node.send(_headers(all_blocks[0]), check_validity=False)
    _send_and_ping(test_node, all_blocks[1])
    _getblock(node0, all_blocks[1])

    # every block but the last, too far ahead of the tip, stored
    for block in all_blocks:
        test_node.send(_block_payload(block), check_validity=False)
    test_node.sync_with_ping()
    for block in all_blocks[:-1]:
        _getblock(node0, block)
    _assert_not_downloaded(node0, all_blocks[-1])

    # 5. the fork's first block again, unrequested: ignored
    _disconnect_p2ps(node0, test_node)
    _disconnect_p2ps(node1, min_work_node)
    test_node = _connected_peer(node0)
    _send_and_ping(test_node, block_h1f)
    assert _block_count(node0) == 2

    # 6. an inv for the fork's third block: the first asked for
    test_node.last_message.pop("getdata", None)
    test_node.send(Inv([Inventory(InventoryType.MSG_BLOCK, block_h3.header.hash)]))
    test_node.sync_with_ping()
    getdata = GetData.parse(test_node.last_message["getdata"].payload)
    assert getdata.items[0].hash == block_h1f.header.hash

    # 7. the fork's first block, requested now: the node reorganises
    _send_and_ping(test_node, block_h1f)
    assert _block_count(node0) == 290
    _getblock(node0, all_blocks[286])
    assert _best_block_hash(node0) == all_blocks[286].header.hash.hex()
    _assert_not_downloaded(node0, all_blocks[287])

    # 8. a fork past the tip whose third block spends an immature coinbase
    block_289f = _child(all_blocks[284], 289)
    block_290f = _child(block_289f, 290)
    block_291 = _child(block_290f, 291, [_immature_spend(block_290f)])
    block_292 = _child(block_291, 292)

    # every header, then the blocks up to the invalid one
    test_node.send(
        _headers(block_289f, block_290f, block_291, block_292), check_validity=False
    )
    test_node.sync_with_ping()
    assert _chain_tip_status(node0, block_292) == "headers-only"
    _assert_not_downloaded(node0, block_292)
    test_node.send(_block_payload(block_289f), check_validity=False)
    _send_and_ping(test_node, block_290f)
    _getblock(node0, block_289f)
    _getblock(node0, block_290f)

    # the invalid block drops the peer, and the node stays where it was
    test_node.send(_block_payload(block_291), check_validity=False)
    test_node.wait_for_disconnect()
    _disconnect_p2ps(node0, test_node)
    test_node = _connected_peer(node0)
    assert _block_count(node0) == 290
    assert _best_block_hash(node0) == all_blocks[286].header.hash.hex()
    assert _getblock(node0, block_291)["confirmations"] == -1

    # a header extending the invalid fork drops the next peer
    block_293 = _child(block_292, 293)
    test_node.send(_headers(block_293), check_validity=False)
    test_node.wait_for_disconnect()
    test_node.close()

    # 9. the second node syncs from the first
    connect_nodes(node0, node1)
    wait_until_tips_agree([node0, node1], timeout=_SYNC_TIMEOUT)
