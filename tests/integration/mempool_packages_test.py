# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `mempool_packages`, one body over each node.

Read from Core's `test/functional/mempool_packages.py` (`fa6f7e36a8c2`,
2026-10-23; `v32.1`'s copy differs only in a comment): the mempool
keeps, for each transaction, the mempool transactions it spends and
those spending it, and the count, size and fees of its ancestors and its
descendants, a prioritisation included; a node under a smaller
`-limitclustercount` holds no more of a family than its limit; and a
reorg returns what a disconnected block held to the mempool.

Core's `run_test` is the body, in its own order, over a pair of nodes,
and every assertion of Core's own is kept:

- a chain of `DEFAULT_CLUSTER_LIMIT` transactions, announced to a peer:
  `getrawmempool`'s verbose entries add up to the chain's own vsize and
  fees; each entry is `getmempoolentry`'s own, and reports its
  descendants' and its ancestors' count, size and fees, the transaction
  it spends and the one spending it; `gettxspendingprevout` names it as
  the spender of each of its inputs; and `getmempoolancestors` and
  `getmempooldescendants`, bare and verbose, list the rest of the chain;
- `prioritisetransaction`'s delta is in the ancestor fees of every
  descendant of the transaction given, and in the descendant fees of
  every ancestor, and a delta given to a transaction a block holds is its
  modified fee once `invalidateblock` returns it to the mempool;
- a parent of ten outputs, and a family spending them up to the default
  count: `getrawmempool` reports the parent's own descendant count and
  children, and the second node, under Core's own `-limitclustercount`
  of ten, holds the parent and no more than its limit, each entry
  agreeing with the first node's and none of them unbroadcast;
- a longer fork submitted over a block returns the block's transactions
  to the mempool, in the order `getrawmempool` gave them before; and a
  spend of two transactions a block holds reaches the second node before
  a fork disconnects that block, both nodes then taking the fork's tip.

`getmempoolancestors`, `getmempooldescendants` and
`gettxspendingprevout` are `Capability.MEMPOOL_GRAPH`'s, asked for first,
then `Capability.INVALIDATE_BLOCK`, `Capability.CONNECT` and
`Capability.MINE`. Both nodes restart with the
`-whitelist=noban,in,out@127.0.0.1` Core's `noban_tx_relay` starts every
node with, which asks for no capability.

Which limit the nodes run is read off the first node's capabilities, as
`mempool_updatefromblock_test.py` reads it: a node declaring
`Capability.CLUSTER_LINEARIZATION` runs the cluster mempool. There the
second node's option is `-limitclustercount`
(`Capability.LIMIT_CLUSTER_COUNT`), asked for last. A build before the
cluster mempool (`v31.0`) has neither the capability nor the option, and
limits by ancestors and descendants, as Core's file of that build does:
the chain and the family are the default 25, the second node is started
with `-limitancestorcount=5` and `-limitdescendantcount=10`, it holds the
first 5 of the chain and then the parent with the first 10 of the family,
and a transaction one past the chain or the family is refused as
`too-long-mempool-chain`.

What differs from Core's file:

- the chain Core's framework caches is mined here by `MiniWallet`
  (`mini_wallet.py`), to the same height; Core's `rescan_utxos` reads
  its wallet's coins off that cached chain, and `MiniWallet.resync`
  here re-reads the tip after each move of the chain this wallet did
  not make;
- a block Core's node mines from its own mempool is built here
  client-side, carrying every transaction the mempool holds, and the
  nodes are then synced, as Core's `generate` syncs them: `_mine_mempool`;
- `build_fork` stands in for Core's `create_empty_fork` (`blocktools.py`),
  its blocks timed off the wall clock rather than one second apart from
  the tip's own time;
- `Peer` (`peer.py`) stands in for Core's `P2PTxInvStore`, and
  `_wait_for_broadcast` for its `wait_for_broadcast`, answering each
  `inv` with a `getdata`, as `P2PInterface.on_inv` does;
- Core's `MiniWallet` returns each transaction's own fee in BTC; the
  fees here are in satoshis, each read off the transaction's own inputs
  and outputs, and every fee the node answers in BTC is converted to
  satoshis before it is compared.

Each `mempool_packages_<node>_test.py` module beside this one runs the body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError, magic_from_chain
from btclib.amount import sats_from_btc
from btclib.p2p import GetData, Inv, InventoryType, Ping, Pong
from btclib.tx import Tx

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet, build_fork
from btclib_functional_tests.node import (
    connect_nodes,
    sync_all,
    wait_until,
    wait_until_mempools_agree,
    wait_until_tips_agree,
)
from btclib_functional_tests.peer import Peer
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.block.block import Block

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.mini_wallet import Utxo
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["mempool_tracks_ancestors_and_descendants"]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

# what Core's own `noban_tx_relay` starts every node with
_NOBAN = "-whitelist=noban,in,out@127.0.0.1"

# the height of the chain Core's own framework caches
_CHAIN_HEIGHT = 200

# Core's own `DEFAULT_CLUSTER_LIMIT` (`test_framework/messages.py`)
_DEFAULT_CLUSTER_LIMIT = 64

# Core's own `CUSTOM_CLUSTER_LIMIT`, the second node's own count
_CUSTOM_CLUSTER_LIMIT = 10

# the build before the cluster mempool: Core's own `DEFAULT_ANCESTOR_LIMIT`
# and `DEFAULT_DESCENDANT_LIMIT`, which a chain and a family reach, and its
# `CUSTOM_ANCESTOR_LIMIT` and `CUSTOM_DESCENDANT_LIMIT`, the second node's
_DEFAULT_ANCESTOR_LIMIT = 25
_CUSTOM_ANCESTOR_LIMIT = 5
_CUSTOM_DESCENDANT_LIMIT = 10

# Core's own `FORK_LENGTH` (`blocktools.py`), `create_empty_fork`'s default
_FORK_LENGTH = 10

# Core's own: the outputs of the family's own parent and of each spend
_FAMILY_OUTPUTS = 10

# Core's own: the chain of spends from the second output of `tx0`
_TX2_TO_TX7 = 6

# Core's own fee per output of `tx8`, in satoshis
_TX8_FEE_PER_OUTPUT = 40_000

# Core's own `fee_delta`s, in satoshis: `Decimal("0.00001")` BTC on a
# transaction in the mempool, `Decimal("0.00002")` on a mined one
_FEE_DELTA = 1000
_MINED_FEE_DELTA = 2000

# Core's own `wait_for_broadcast` default, in seconds
_BROADCAST_TIMEOUT = 60.0

# Core's own wait for the second node's own mempool, in seconds
_SECOND_MEMPOOL_TIMEOUT = 10.0

_TX_TYPES = frozenset({InventoryType.MSG_TX, InventoryType.MSG_WTX})


def _mempool(node: NodeAdapter) -> list[str]:
    """Return the txids `node`'s own `getrawmempool` answers, in its order."""
    txids = node.rpc.call("getrawmempool")
    assert isinstance(txids, list)
    return txids


def _fees(entry: dict[str, object]) -> dict[str, int]:
    """Return a mempool entry's own `fees`, in satoshis."""
    fees = entry["fees"]
    assert isinstance(fees, dict)
    return {key: sats_from_btc(value) for key, value in fees.items()}


def _fee(tx: Tx, spent: Sequence[Utxo]) -> int:
    """Return the fee `tx` pays, spending `spent`, in satoshis."""
    return sum(utxo.value for utxo in spent) - sum(out.value for out in tx.vout)


def _wait_for_broadcast(peer: Peer, wtxids: set[bytes]) -> None:
    """Core's `P2PTxInvStore.wait_for_broadcast`, requesting what is announced.

    Returns once the transactions `peer` has seen announced are exactly
    `wtxids`, then syncs with a ping so that the node has served each
    `getdata` sent meanwhile.

    :raises TimeoutError: the announcements seen still differ from
        `wtxids` after `_BROADCAST_TIMEOUT`, scaled.
    """
    announced: set[bytes] = set()
    deadline = time.monotonic() + scaled(_BROADCAST_TIMEOUT)
    while announced != wtxids:
        remaining = deadline - time.monotonic()
        message = None
        if remaining > 0:
            with suppress(TimeoutError):
                message = peer.receive(timeout=remaining)
        if message is None:
            missing = sorted(wtxid.hex() for wtxid in wtxids - announced)
            unexpected = sorted(wtxid.hex() for wtxid in announced - wtxids)
            err_msg = f"never announced {missing}, announced {unexpected} besides"
            raise TimeoutError(err_msg)
        if message.command == "ping":
            peer.send(Pong(Ping.parse(message.payload).nonce))
        elif message.command == "inv":
            items = Inv.parse(message.payload).items
            if wanted := [
                item for item in items if item.type_code != InventoryType.UNDEFINED
            ]:
                peer.send(GetData(wanted))
            announced |= {item.hash for item in items if item.type_code in _TX_TYPES}
    peer.sync_with_ping()


def _mine_mempool(nodes: Sequence[NodeAdapter], wallet: MiniWallet) -> None:
    """Core's `generate` on the first node: mine its mempool, then sync.

    The block carries every transaction the first node's mempool holds,
    a parent ahead of each child `getrawmempool`'s own `depends` names;
    then every node's tip, and every node's mempool, agree.
    """
    entries = nodes[0].rpc.call("getrawmempool", [True])
    assert isinstance(entries, dict)
    ordered: list[str] = []
    while len(ordered) < len(entries):
        ordered += [
            txid
            for txid, entry in entries.items()
            if txid not in ordered and set(entry["depends"]) <= set(ordered)
        ]
    txs = [
        Tx.parse(
            bytes.fromhex(nodes[0].rpc.call("getrawtransaction", [txid])),
            check_validity=False,
        )
        for txid in ordered
    ]
    wallet.generate(1, confirm=txs)
    sync_all(nodes)


def _trigger_reorg(fork_blocks: Sequence[Block], node: NodeAdapter) -> None:
    """Core's `trigger_reorg`: submit the fork, which becomes the tip."""
    for block in fork_blocks:
        node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    assert node.rpc.call("getbestblockhash") == fork_blocks[-1].header.hash.hex()


def _check_chain(
    node: NodeAdapter, chain_txs: Sequence[Tx], fees: Sequence[int]
) -> None:
    """Core's walk down the chain: each entry, its spenders and its relatives.

    :param chain_txs: the chain, the first spending a confirmed coin and
        each of the rest spending the one before it.
    :param fees: each transaction's own fee, in satoshis.
    """
    mempool = node.rpc.call("getrawmempool", [True])
    assert len(mempool) == len(chain_txs)
    descendant_fees = 0
    descendant_vsize = 0

    ancestor_vsize = sum(tx.vsize for tx in chain_txs)
    assert ancestor_vsize == sum(entry["vsize"] for entry in mempool.values())
    ancestor_count = len(chain_txs)
    ancestor_fees = sum(fees)
    assert ancestor_fees == sum(_fees(entry)["base"] for entry in mempool.values())

    descendants: list[str] = []
    chain = [tx.id.hex() for tx in chain_txs]
    ancestors = list(chain)
    for descendant_count, x in enumerate(reversed(chain), start=1):
        # getmempoolentry is consistent with getrawmempool
        entry = node.rpc.call("getmempoolentry", [x])
        assert entry == mempool[x]

        # gettxspendingprevout is consistent with getrawmempool
        witnesstx = node.rpc.call("getrawtransaction", [x, True])
        for tx_in in witnesstx["vin"]:
            outpoint = {"txid": tx_in["txid"], "vout": tx_in["vout"]}
            spending_result = node.rpc.call("gettxspendingprevout", [[outpoint]])
            assert spending_result == [{**outpoint, "spendingtxid": x}]

        # the descendant calculations are correct
        entry_fees = _fees(entry)
        assert entry["descendantcount"] == descendant_count
        descendant_fees += entry_fees["base"]
        assert entry_fees["modified"] == entry_fees["base"]
        assert entry_fees["descendant"] == descendant_fees
        descendant_vsize += entry["vsize"]
        assert entry["descendantsize"] == descendant_vsize

        # the ancestor calculations are correct
        assert entry["ancestorcount"] == ancestor_count
        assert entry_fees["ancestor"] == ancestor_fees
        assert entry["ancestorsize"] == ancestor_vsize
        ancestor_vsize -= entry["vsize"]
        ancestor_fees -= entry_fees["base"]
        ancestor_count -= 1

        # the parent and child lists are correct
        assert entry["spentby"] == descendants[-1:]
        assert entry["depends"] == ancestors[-2:-1]

        # getmempooldescendants is correct, bare and verbose
        assert sorted(descendants) == sorted(
            node.rpc.call("getmempooldescendants", [x])
        )
        verbose = node.rpc.call("getmempooldescendants", [x, True])
        for descendant, dinfo in verbose.items():
            assert dinfo["depends"] == [chain[chain.index(descendant) - 1]]
            if dinfo["descendantcount"] > 1:
                assert dinfo["spentby"] == [chain[chain.index(descendant) + 1]]
            else:
                assert dinfo["spentby"] == []
        descendants.append(x)

        # getmempoolancestors is correct, bare and verbose
        ancestors.remove(x)
        assert sorted(ancestors) == sorted(node.rpc.call("getmempoolancestors", [x]))
        verbose = node.rpc.call("getmempoolancestors", [x, True])
        for ancestor, ainfo in verbose.items():
            assert ainfo["spentby"] == [chain[chain.index(ancestor) + 1]]
            if ainfo["ancestorcount"] > 1:
                assert ainfo["depends"] == [chain[chain.index(ancestor) - 1]]
            else:
                assert ainfo["depends"] == []

    # the verbose answers are getrawmempool's own entries
    v_ancestors = node.rpc.call("getmempoolancestors", [chain[-1], True])
    assert len(v_ancestors) == len(chain) - 1
    for x in v_ancestors:
        assert mempool[x] == v_ancestors[x]
    assert chain[-1] not in v_ancestors

    v_descendants = node.rpc.call("getmempooldescendants", [chain[0], True])
    assert len(v_descendants) == len(chain) - 1
    for x in v_descendants:
        assert mempool[x] == v_descendants[x]
    assert chain[0] not in v_descendants


def _check_prioritisation(
    nodes: Sequence[NodeAdapter], wallet: MiniWallet, chain: list[str]
) -> None:
    """Core's checks of `prioritisetransaction`, in the mempool and mined."""
    node0, node1 = nodes

    # the ancestor fees include a delta on an ancestor
    node0.rpc.call("prioritisetransaction", [chain[0], 0, _FEE_DELTA])
    ancestor_fees = 0
    for x in chain:
        entry_fees = _fees(node0.rpc.call("getmempoolentry", [x]))
        ancestor_fees += entry_fees["base"]
        assert entry_fees["ancestor"] == ancestor_fees + _FEE_DELTA

    # undone for the checks below
    node0.rpc.call("prioritisetransaction", [chain[0], 0, -_FEE_DELTA])

    # the descendant fees include a delta on a descendant
    node0.rpc.call("prioritisetransaction", [chain[-1], 0, _FEE_DELTA])
    descendant_fees = 0
    for x in reversed(chain):
        entry_fees = _fees(node0.rpc.call("getmempoolentry", [x]))
        descendant_fees += entry_fees["base"]
        assert entry_fees["descendant"] == descendant_fees + _FEE_DELTA

    # a delta given to a mined transaction is there once a reorg returns it
    _mine_mempool(nodes, wallet)
    assert len(_mempool(node0)) == 0
    node0.rpc.call("prioritisetransaction", [chain[-1], 0, _MINED_FEE_DELTA])
    node0.rpc.call("invalidateblock", [node0.rpc.call("getbestblockhash")])
    # the second node's tip is kept at the first node's own
    node1.rpc.call("invalidateblock", [node1.rpc.call("getbestblockhash")])
    wallet.resync()

    descendant_fees = 0
    for x in reversed(chain):
        entry_fees = _fees(node0.rpc.call("getmempoolentry", [x]))
        descendant_fees += entry_fees["base"]
        if x == chain[-1]:
            assert entry_fees["modified"] == entry_fees["base"] + _MINED_FEE_DELTA
        assert entry_fees["descendant"] == descendant_fees + _MINED_FEE_DELTA


def _assert_second_node_agrees(
    node0: NodeAdapter, node1: NodeAdapter, txids: Sequence[str]
) -> None:
    """Assert `node1` holds each of `txids` as `node0` does."""
    for txid in txids:
        entry0 = node0.rpc.call("getmempoolentry", [txid])
        entry1 = node1.rpc.call("getmempoolentry", [txid])
        assert not entry0["unbroadcast"]
        assert not entry1["unbroadcast"]
        assert entry1["fees"]["base"] == entry0["fees"]["base"]
        assert entry1["vsize"] == entry0["vsize"]
        assert entry1["depends"] == entry0["depends"]


def _check_chain_limit(
    node: NodeAdapter, wallet: MiniWallet, chain: Sequence[Tx]
) -> None:
    """Core's refusal of one more transaction on a full chain, pre-cluster."""
    one_more = wallet.create_self_transfer(utxo_to_spend=wallet.new_utxos(chain[-1])[0])
    with pytest.raises(RpcError, match="too-long-mempool-chain"):
        node.rpc.call("sendrawtransaction", [_hex(one_more)])


def _check_ancestor_limit(nodes: Sequence[NodeAdapter], chain: Sequence[str]) -> None:
    """Core's check of the second node under its ancestor limit, pre-cluster."""
    node0, node1 = nodes
    mempool1 = _mempool(node1)
    assert len(mempool1) == _CUSTOM_ANCESTOR_LIMIT
    assert set(mempool1) <= set(_mempool(node0))
    held = chain[:_CUSTOM_ANCESTOR_LIMIT]
    assert set(held) <= set(mempool1)
    _assert_second_node_agrees(node0, node1, held)


def _check_descendant_limit(
    nodes: Sequence[NodeAdapter], parent: str, family: Sequence[str], next_hop: Tx
) -> None:
    """Core's check of the second node under its descendant limit, pre-cluster.

    :param parent: the family's own parent.
    :param family: the transactions chained off `parent`, in the order sent.
    :param next_hop: a transaction one past the default descendant limit.
    """
    node0, node1 = nodes
    with pytest.raises(RpcError, match="too-long-mempool-chain"):
        node0.rpc.call("sendrawtransaction", [_hex(next_hop)])

    wait_until(
        lambda: (
            len(_mempool(node1))
            == _CUSTOM_ANCESTOR_LIMIT + 1 + _CUSTOM_DESCENDANT_LIMIT
        ),
        timeout=_SECOND_MEMPOOL_TIMEOUT,
    )
    mempool1 = _mempool(node1)
    assert set(mempool1) <= set(_mempool(node0))
    assert parent in mempool1
    held = family[:_CUSTOM_DESCENDANT_LIMIT]
    assert set(held) <= set(mempool1)
    assert not set(family[_CUSTOM_DESCENDANT_LIMIT:]) & set(mempool1)
    _assert_second_node_agrees(node0, node1, mempool1)


def _hex(tx: Tx) -> str:
    """Return `tx` serialized with its witness."""
    return tx.serialize(True, check_validity=False).hex()


def _check_family(
    nodes: Sequence[NodeAdapter], wallet: MiniWallet, *, clustered: bool
) -> None:
    """Core's descendant limit checks, on both nodes.

    :param clustered: whether the nodes run the cluster mempool.
    """
    node0, node1 = nodes
    limit = _DEFAULT_CLUSTER_LIMIT if clustered else _DEFAULT_ANCESTOR_LIMIT

    # one parent, and its family up to the default count
    tx_with_children = wallet.send_self_transfer_multi(num_outputs=_FAMILY_OUTPUTS)
    parent_transaction = tx_with_children.id.hex()
    transaction_package = wallet.new_utxos(tx_with_children)

    tx_children: list[str] = []
    family: list[str] = []
    for _ in range(limit - 1):
        utxo = transaction_package.pop(0)
        new_tx = wallet.send_self_transfer_multi(
            num_outputs=_FAMILY_OUTPUTS, utxos_to_spend=[utxo]
        )
        txid = new_tx.id.hex()
        family.append(txid)
        if utxo.outpoint.tx_id == tx_with_children.id:
            tx_children.append(txid)
        transaction_package.extend(wallet.new_utxos(new_tx))

    mempool = node0.rpc.call("getrawmempool", [True])
    assert mempool[parent_transaction]["descendantcount"] == limit
    assert sorted(mempool[parent_transaction]["spentby"]) == sorted(tx_children)
    for child in tx_children:
        assert mempool[child]["depends"] == [parent_transaction]

    if not clustered:
        next_hop = wallet.create_self_transfer(utxo_to_spend=transaction_package.pop(0))
        _check_descendant_limit(nodes, parent_transaction, family, next_hop)
        return

    # the second node holds the parent and as much of the family as its
    # own count lets it, beside as much of the chain
    wait_until(
        lambda: len(_mempool(node1)) == 2 * _CUSTOM_CLUSTER_LIMIT,
        timeout=_SECOND_MEMPOOL_TIMEOUT,
    )
    mempool0 = set(_mempool(node0))
    mempool1 = set(_mempool(node1))
    assert mempool1 <= mempool0
    assert parent_transaction in mempool1
    for tx in family:
        if tx in mempool1:
            entry0 = node0.rpc.call("getmempoolentry", [tx])
            entry1 = node1.rpc.call("getmempoolentry", [tx])
            assert not entry0["unbroadcast"]
            assert not entry1["unbroadcast"]
            assert entry1["descendantcount"] <= _CUSTOM_CLUSTER_LIMIT
            assert entry1["fees"]["base"] == entry0["fees"]["base"]
            assert entry1["vsize"] == entry0["vsize"]
            assert entry1["depends"] == entry0["depends"]


def _check_reorgs(nodes: Sequence[NodeAdapter], wallet: MiniWallet) -> None:
    """Core's reorg handling, the basics and then a spend of a mined pair."""
    node0 = nodes[0]

    # a reorg returns the transactions, in the same order
    fork_blocks = build_fork(node0, wallet.script_pub_key, _FORK_LENGTH)
    mempool0 = _mempool(node0)
    _mine_mempool(nodes, wallet)
    _trigger_reorg(fork_blocks, node0)
    wallet.resync()
    assert _mempool(node0) == mempool0

    # the mempool cleaned up
    _mine_mempool(nodes, wallet)

    # tx0 -> tx1 (vout0)
    #   \--> tx2 (vout1) -> tx3 -> tx4 -> tx5 -> tx6 -> tx7
    # mined in a block a fork then disconnects, with tx8, spending tx1 and
    # tx7, in both mempools
    fork_blocks = build_fork(node0, wallet.script_pub_key, _FORK_LENGTH)
    tx0 = wallet.send_self_transfer_multi(num_outputs=2)
    tx0_utxos = wallet.new_utxos(tx0)
    tx1 = wallet.send_self_transfer(utxo_to_spend=tx0_utxos[0])
    tx7 = wallet.send_self_transfer_chain(
        utxo_to_spend=tx0_utxos[1], chain_length=_TX2_TO_TX7
    )[-1]
    _mine_mempool(nodes, wallet)

    wallet.send_self_transfer_multi(
        utxos_to_spend=[wallet.new_utxos(tx1)[0], wallet.new_utxos(tx7)[0]],
        fee_per_output=_TX8_FEE_PER_OUTPUT,
    )
    wait_until_mempools_agree(nodes)

    # the tip disconnected on each node
    _trigger_reorg(fork_blocks, node0)
    wait_until_tips_agree(nodes)


def mempool_tracks_ancestors_and_descendants(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes = cluster(2)
    node0, node1 = nodes
    require(Capability.MEMPOOL_GRAPH, node0.capabilities, skip_counts)
    require(Capability.INVALIDATE_BLOCK, node0.capabilities, skip_counts)
    require(Capability.CONNECT, node0.capabilities, skip_counts)
    require(Capability.MINE, node0.capabilities, skip_counts)
    clustered = Capability.CLUSTER_LINEARIZATION in node0.capabilities
    if clustered:
        require(Capability.LIMIT_CLUSTER_COUNT, node1.capabilities, skip_counts)
    limit = _DEFAULT_CLUSTER_LIMIT if clustered else _DEFAULT_ANCESTOR_LIMIT
    node0.restart([_NOBAN])
    if clustered:
        node1_limits = [f"-limitclustercount={_CUSTOM_CLUSTER_LIMIT}"]
    else:
        node1_limits = [
            f"-limitancestorcount={_CUSTOM_ANCESTOR_LIMIT}",
            f"-limitdescendantcount={_CUSTOM_DESCENDANT_LIMIT}",
        ]
    node1.restart([*node1_limits, _NOBAN])
    connect_nodes(node1, node0)
    wallet = MiniWallet(node0)
    wallet.generate(_CHAIN_HEIGHT)
    sync_all(nodes)

    with Peer(node0.p2p_address, _MAGIC) as peer_inv_store:
        peer_inv_store.handshake()
        peer_inv_store.sync_with_ping()

        # the default count of transactions off a confirmed coin
        first = wallet.get_utxo()
        chain = wallet.send_self_transfer_chain(chain_length=limit, utxo_to_spend=first)
        spent = [first, *(wallet.new_utxos(tx)[0] for tx in chain[:-1])]
        fees = [_fee(tx, [utxo]) for tx, utxo in zip(chain, spent, strict=True)]

        # past the initial broadcast, so that no entry's own unbroadcast
        # changes between getrawmempool and getmempoolentry
        _wait_for_broadcast(peer_inv_store, {tx.hash for tx in chain})

        _check_chain(node0, chain, fees)
        if not clustered:
            _check_chain_limit(node0, wallet, chain)
        chain_ids = [tx.id.hex() for tx in chain]
        _check_prioritisation(nodes, wallet, chain_ids)
        if not clustered:
            _check_ancestor_limit(nodes, chain_ids)
        _check_family(nodes, wallet, clustered=clustered)
        _check_reorgs(nodes, wallet)
