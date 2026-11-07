# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_getblockfrompeer`, one body over each node.

Read from Core's `test/functional/rpc_getblockfrompeer.py`
(`779f4446803d`, 2026-05-25): three nodes, the third under
`-fastprune -prune=1` (`Capability.FASTPRUNE`). The first two mine
competing chains (`Capability.MINE`) and are connected
(`Capability.CONNECT`), so that the first holds only the header of the
second's own tip; `getblockfrompeer` (`Capability.BLOCK_FROM_PEER`)
then fetches it from the second, after refusing a malformed hash, a
wrongly typed argument, a header it lacks, a peer that does not exist
and a pre-segwit `Peer` (`peer.py`). The second node, restarted under
`-prune=550`, refuses to fetch a block past its own tip from a `Peer`
that sent only the header. The pruned third node fetches a block it
pruned, keeps it through the next `pruneblockchain`, and drops it once
the prune height passes the tip it had when the block was fetched.

`-prune` asks for no capability: `cli.py` registers it and
`pruneblockchain` names a callback in `rpc/callbacks.py`'s own dispatch
table, at btclib-node's released `2026.9.24` and at its `main`
(`25776772`) alike.

Core starts every node on its cached 200-block chain; this mines 200
blocks on the first node and submits them to the other two, a chain of the
same height and not the same blocks, so Core's literal hash for the
fetched block is compared with the first node's own `getblockhash`
instead. Core's `pruneblockchain` heights are literals too, and differ
between `v30.3` and the pin (`git diff v30.3 779f4446803d --
test/functional/rpc_getblockfrompeer.py`): `pruneblockchain` removes whole
block files and answers the highest block left without data
(`GetPruneHeight`, `src/rpc/blockchain.cpp`), so its answer moves with
where each build's files end. This asserts what both sets of literals
share instead: each prune height is under the height asked for and above
the one before, the fetched block survives a prune height below the tip it
was fetched at, and is pruned again by one at or past it.
Each `rpc_getblockfrompeer_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError, magic_from_chain
from btclib.block.block import Block
from btclib.p2p import Headers, ServiceFlags

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import (
    connect_nodes,
    wait_until,
    wait_until_tips_agree,
)
from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["getblockfrompeer_fetches_what_the_node_lacks"]

_MAGIC = magic_from_chain("regtest")

# the height of Core's own cached chain, every node's start
_CACHED_HEIGHT = 200

# how many blocks one `mine` call asks for: a bitcoind's own
# `generatetoaddress` answers only once every block is mined, and several
# hundred in one call outlast the RPC client's own timeout on `v32.0rc2`
_MINE_CHUNK = 50

_PRUNE_MODE_REFUSAL = (
    "In prune mode, only blocks that the node has already synced previously"
    " can be fetched from a peer"
)


def _has_block(node: NodeAdapter, block_hash: str) -> bool:
    """Core's own `check_for_block`: whether `getblock` answers."""
    try:
        node.rpc.call("getblock", [block_hash])
    except RpcError:
        return False
    return True


def _refused(
    node: NodeAdapter, method: str, params: list[object], code: int, message: str
) -> None:
    """Core's own `assert_raises_rpc_error`: `message` a substring, as there."""
    with pytest.raises(RpcError) as excinfo:
        node.rpc.call(method, params)
    assert excinfo.value.code == code
    assert message in str(excinfo.value)


def _mine(node: NodeAdapter, count: int) -> None:
    """Mine `count` blocks on `node`, at most `_MINE_CHUNK` per call."""
    while count > 0:
        node.mine(min(count, _MINE_CHUNK))
        count -= _MINE_CHUNK


def _submit_chain(source: NodeAdapter, targets: Sequence[NodeAdapter]) -> None:
    """Hand every block `source` holds past genesis to each of `targets`."""
    for height in range(1, source.rpc.call("getblockcount") + 1):
        block_hash = source.rpc.call("getblockhash", [height])
        block_hex = source.rpc.call("getblock", [block_hash, 0])
        for target in targets:
            assert target.rpc.call("submitblock", [block_hex]) is None


def getblockfrompeer_fetches_what_the_node_lacks(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `run_test`, in Core's own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node0, node1, pruned_node = cluster(3)
    require(Capability.BLOCK_FROM_PEER, node0.capabilities, skip_counts)
    require(Capability.FASTPRUNE, pruned_node.capabilities, skip_counts)
    require(Capability.MINE, node0.capabilities, skip_counts)
    require(Capability.MINE, node1.capabilities, skip_counts)
    require(Capability.CONNECT, node0.capabilities, skip_counts)
    pruned_node.restart(["-fastprune", "-prune=1"])
    _mine(node0, _CACHED_HEIGHT)
    _submit_chain(node0, [node1, pruned_node])

    # competing chains: four blocks on node 0, three on node 1
    node0.mine(4)
    assert node0.rpc.call("getblockcount") == _CACHED_HEIGHT + 4
    node1.mine(3)
    assert node1.rpc.call("getblockcount") == _CACHED_HEIGHT + 3
    short_tip = node1.rpc.call("getbestblockhash")

    connect_nodes(node0, node1)
    wait_until_tips_agree([node0, node1])

    # node 0 has only the header of node 1's own tip
    (tip,) = [tip for tip in node0.rpc.call("getchaintips") if tip["hash"] == short_tip]
    assert tip["status"] == "headers-only"
    _refused(
        node0,
        "getblock",
        [short_tip],
        -1,
        "Block not available (not fully downloaded)",
    )

    peers = node0.rpc.call("getpeerinfo")
    assert len(peers) == 1
    node1_id = peers[0]["id"]

    # arguments must be valid
    _refused(
        node0,
        "getblockfrompeer",
        ["1234", node1_id],
        -8,
        "hash must be of length 64 (not 4, for '1234')",
    )
    _refused(
        node0,
        "getblockfrompeer",
        [1234, node1_id],
        -3,
        "JSON value of type number is not of expected type string",
    )
    _refused(
        node0,
        "getblockfrompeer",
        [short_tip, "0"],
        -3,
        "JSON value of type string is not of expected type number",
    )
    # the header must be known
    _refused(node0, "getblockfrompeer", ["00" * 32, 0], -1, "Block header missing")
    for peer_id in (-1, node1_id + 1):
        _refused(
            node0, "getblockfrompeer", [short_tip, peer_id], -1, "Peer does not exist"
        )

    with Peer(node0.p2p_address, _MAGIC) as presegwit:
        presegwit.handshake(services=ServiceFlags.NODE_NETWORK)
        presegwit.sync_with_ping()
        peers = node0.rpc.call("getpeerinfo")
        assert len(peers) == 2
        (presegwit_id,) = [peer["id"] for peer in peers if peer["id"] != node1_id]
        _refused(
            node0,
            "getblockfrompeer",
            [short_tip, presegwit_id],
            -1,
            "Pre-SegWit peer",
        )

    # a successful fetch, and none of a block already held
    assert node0.rpc.call("getblockfrompeer", [short_tip, node1_id]) == {}
    wait_until(lambda: _has_block(node0, short_tip))
    _refused(
        node0,
        "getblockfrompeer",
        [short_tip, node1_id],
        -1,
        "Block already downloaded",
    )

    # a pruning node fetches no block past what it has synced; the restart
    # also disconnects node 1 from node 0, as Core's own does
    node1.restart(["-prune=550"])
    (block_hash,) = node0.mine(1)
    block = Block.parse(
        bytes.fromhex(node0.rpc.call("getblock", [block_hash, 0])),
        check_validity=False,
    )
    with Peer(node1.p2p_address, _MAGIC) as header_only:
        header_only.handshake()
        header_only.sync_with_ping()
        header_only.send(Headers([block.header]), check_validity=False)
        header_only.sync_with_ping()
        node1_peers = node1.rpc.call("getpeerinfo")
        assert len(node1_peers) == 1
        _refused(
            node1,
            "getblockfrompeer",
            [block_hash, node1_peers[0]["id"]],
            -1,
            _PRUNE_MODE_REFUSAL,
        )

    # the pruned node, synced and pruned
    connect_nodes(node0, pruned_node)
    wait_until_tips_agree([node0, pruned_node])
    _mine(node0, 400)
    wait_until_tips_agree([node0, pruned_node])
    first_prune = pruned_node.rpc.call("pruneblockchain", [300])
    pruned_block = node0.rpc.call("getblockhash", [2])
    assert 2 <= first_prune < 300
    _refused(
        pruned_node,
        "getblock",
        [pruned_block],
        -1,
        "Block not available (pruned data)",
    )

    # the pruned block, fetched back
    peers = pruned_node.rpc.call("getpeerinfo")
    assert len(peers) == 1
    fetched_at = pruned_node.rpc.call("getblockcount")
    assert (
        pruned_node.rpc.call("getblockfrompeer", [pruned_block, peers[0]["id"]]) == {}
    )
    wait_until(lambda: _has_block(pruned_node, pruned_block))

    # it survives a prune stopping short of the tip it was fetched at
    _mine(node0, 250)
    wait_until_tips_agree([node0, pruned_node])
    second_prune = pruned_node.rpc.call("pruneblockchain", [700])
    assert first_prune < second_prune < fetched_at
    assert pruned_node.rpc.call("getblock", [pruned_block])["hash"] == pruned_block

    # and is pruned again once the prune height passes that tip
    _mine(node0, 250)
    wait_until_tips_agree([node0, pruned_node])
    third_prune = pruned_node.rpc.call("pruneblockchain", [1000])
    assert fetched_at <= third_prune < 1000
    _refused(
        pruned_node,
        "getblock",
        [pruned_block],
        -1,
        "Block not available (pruned data)",
    )
