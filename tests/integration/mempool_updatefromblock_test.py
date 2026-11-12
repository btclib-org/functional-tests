# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `mempool_updatefromblock`, one body per test over each node.

Read from Core's `test/functional/mempool_updatefromblock.py`
(`fa6b05c96ffb`, 2026-03-12) and narrowed to what the option and
MiniWallet families reach together
([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)):
`-limitclustersize` (`Capability.LIMIT_CLUSTER_SIZE`) is the one option
either kept subtest configures, and `MiniWallet.create_self_transfer_multi`
/`create_self_transfer_chain` are what build the graphs Core's own file
reorgs back into the mempool.

A smaller claim than Core's own file: kept is the acyclic-tournament
case (`transaction_graph_test`, a much smaller tournament than Core's
own `DEFAULT_CLUSTER_LIMIT`-sized one, the mechanism -- every
mempool-entry's own ancestor/descendant count and size recomputed after
a reorg re-adds every transaction a mined block once carried -- needing
no particular size to hold) and the chain-length case
(`test_chainlimits_exceeded`: it is the node's own *default* chain
limit, not an option this file configures, that the chain has to
exceed). Dropped is Core's own `test_max_disconnect_pool_bytes`:
`MAX_DISCONNECTED_TX_POOL_BYTES` is a fixed 20-megabyte bound in
bitcoind's own C++, not a configurable option, so exercising it means
actually building, mining and reorging some twenty megabytes of
transactions -- disproportionate for a boundary neither kept subtest
needs to also cover.

Each body starts its node without `-limitclustersize` and, where the
node declares the capability, restarts it with the option before any
block is mined. Which limit a chain meets is read off the node's
capabilities: a node declaring `Capability.CLUSTER_LINEARIZATION` runs
the cluster mempool, whether or not it takes the option. btclib-node
declares it and takes no `-limitclustersize`. A build before the cluster
mempool (`v31.0`) has neither the capability nor the option, and limits
a chain by its ancestors instead: the tournament runs there as it is,
within the default ancestor and descendant limits, and the chain is the
default of 25 and two more, refused as `too-long-mempool-chain`.

Each `mempool_updatefromblock_<node>_test.py` module beside this one
runs each body, `tests/integration/conftest.py`'s own module docstring
having how.
"""

from __future__ import annotations

import re
from math import ceil
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import FEE, MiniWallet, build_fork

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.tx import Tx

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_chain_over_the_default_cluster_limit_needs_a_reorg_to_fit",
    "reorg_recomputes_every_entry_s_own_ancestors_and_descendants",
]

# Core's own file reorgs a chain longer than this, exercised here at a
# fraction of Core's own DEFAULT_CLUSTER_LIMIT-sized graph: the tournament
# mechanism -- every entry's ancestor/descendant count and size recomputed
# once a reorg re-adds every transaction a mined block once carried -- does
# not need a particular size to hold
_TOURNAMENT_SIZE = 6

# bitcoind's own compiled-in default -limitclustercount, exercised by
# `test_chainlimits_exceeded` with no flag of its own: this file's only
# configured option is -limitclustersize, set high enough that the count
# default -- not the configured size -- is what the chain has to exceed
_DEFAULT_CLUSTER_LIMIT = 64

# the build before the cluster mempool's own compiled-in default
# -limitancestorcount, which a chain has to exceed there instead
_DEFAULT_ANCESTOR_LIMIT = 25


def _start(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    *needs: Capability,
) -> tuple[NodeAdapter, bool]:
    """Return one node and whether it runs the cluster mempool.

    A node declaring `Capability.LIMIT_CLUSTER_SIZE` is restarted with
    `-limitclustersize=1000`. Whether it runs the cluster mempool is
    whether it declares `Capability.CLUSTER_LINEARIZATION`, not
    `Capability.LIMIT_CLUSTER_SIZE`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    :param needs: what the body asks for beyond `Capability.MINE` and
        `Capability.MEMPOOL_ENTRY`, each asked for ahead of them.
    """
    (node,) = cluster(1)
    for capability in needs:
        require(capability, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    require(Capability.MEMPOOL_ENTRY, node.capabilities, skip_counts)
    clustered = Capability.CLUSTER_LINEARIZATION in node.capabilities
    if Capability.LIMIT_CLUSTER_SIZE in node.capabilities:
        node.restart(["-limitclustersize=1000"])
    return node, clustered


def reorg_recomputes_every_entry_s_own_ancestors_and_descendants(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check an acyclic tournament's own mempool entries survive a reorg intact.

    tx[K] of a `_TOURNAMENT_SIZE`-transaction tournament has
    `_TOURNAMENT_SIZE - K` descendants (itself included) and `K + 1`
    ancestors: built with some mined into blocks along the way, then
    resurrected whole by a reorg longer than any of those blocks, every
    entry's own `getmempoolentry` answer still holds.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _start(cluster, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 1)
    # built before any tournament transaction exists, and longer than
    # however many blocks the tournament mines along the way, so that
    # submitting it later reorgs every one of them back into the mempool
    fork_blocks = build_fork(node, wallet.script_pub_key, 7)

    mine_after = {2, 4}
    tx_ids: list[str] = []
    tx_vsizes: list[int] = []
    unconfirmed: list[Tx] = []
    for i in range(_TOURNAMENT_SIZE):
        if i == 0:
            tx = wallet.send_self_transfer_multi(
                num_outputs=_TOURNAMENT_SIZE - 1,
                fee_per_output=ceil(FEE / (_TOURNAMENT_SIZE - 1)),
            )
        else:
            inputs = [wallet.get_utxo(txid=tx_ids[j], vout=i - j - 1) for j in range(i)]
            n_outputs = _TOURNAMENT_SIZE - (i + 1) or 1
            tx = wallet.send_self_transfer_multi(
                utxos_to_spend=inputs,
                num_outputs=n_outputs,
                fee_per_output=ceil(FEE / n_outputs),
            )
        tx_ids.append(tx.id.hex())
        tx_vsizes.append(tx.vsize)
        unconfirmed.append(tx)
        tx_count = i + 1
        if tx_count in mine_after:
            # BtclibNodeAdapter.mine mines the coinbase alone;
            # bitcoind's takes the mempool
            wallet.generate(1, confirm=unconfirmed)
            unconfirmed.clear()
            assert node.rpc.call("getmempoolinfo")["size"] == 0

    for block in fork_blocks:
        node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    assert node.rpc.call("getmempoolinfo")["size"] == _TOURNAMENT_SIZE

    for k, txid in enumerate(tx_ids):
        entry = node.rpc.call("getmempoolentry", [txid])
        assert entry["descendantcount"] == _TOURNAMENT_SIZE - k, (k, entry)
        assert entry["descendantsize"] == sum(tx_vsizes[k:_TOURNAMENT_SIZE]), (
            k,
            entry,
        )
        assert entry["ancestorcount"] == k + 1, (k, entry)
        assert entry["ancestorsize"] == sum(tx_vsizes[: k + 1]), (k, entry)


def a_chain_over_the_default_cluster_limit_needs_a_reorg_to_fit(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check a chain over the node's default chain limit is refused entry.

    The node's default chain limit is in force, the cluster count where
    the node has one. Of a chain two longer than it, the first `limit`
    transactions enter the mempool and the next one is refused by
    `sendrawtransaction`. All but the last are accepted into one block by
    `generateblock` naming the raw transactions directly (bypassing
    mempool admission entirely). Every transaction but the chain's last
    two is resurrected into the mempool once a longer fork reorgs that
    block away, back under the limit the chain never fit unconfirmed.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, clustered = _start(cluster, skip_counts, Capability.GENERATE)
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 1)
    fork_blocks = build_fork(node, wallet.script_pub_key, 10)

    limit = _DEFAULT_CLUSTER_LIMIT if clustered else _DEFAULT_ANCESTOR_LIMIT
    refusal = (
        "too-large-cluster"
        if clustered
        else "too-long-mempool-chain, too many unconfirmed ancestors "
        f"[limit: {_DEFAULT_ANCESTOR_LIMIT}]"
    )
    chain = wallet.create_self_transfer_chain(chain_length=limit + 2)
    for tx in chain[:-2]:
        node.rpc.call(
            "sendrawtransaction", [tx.serialize(True, check_validity=False).hex()]
        )
    with pytest.raises(RpcError, match=re.escape(refusal)):
        node.rpc.call(
            "sendrawtransaction",
            [chain[-2].serialize(True, check_validity=False).hex()],
        )

    node.rpc.call(
        "generateblock",
        [
            "raw(42)",
            [tx.serialize(True, check_validity=False).hex() for tx in chain[:-1]],
        ],
    )
    assert node.rpc.call("getrawmempool") == []

    node.rpc.call(
        "sendrawtransaction",
        [chain[-1].serialize(True, check_validity=False).hex()],
    )

    for block in fork_blocks:
        node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    mempool = node.rpc.call("getrawmempool")
    assert set(mempool) == {tx.id.hex() for tx in chain[:-2]}
