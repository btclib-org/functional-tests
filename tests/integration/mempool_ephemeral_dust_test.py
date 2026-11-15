# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `mempool_ephemeral_dust`, as bodies over each node.

Read from Core's `test/functional/mempool_ephemeral_dust.py`
(`fa6f7e36a8c2`, 2026-10-23; `v32.1`'s copy differs only in a comment
and a blank line): a transaction paying no fee may carry one dust output
where a child in the same package spends it, `CheckEphemeralSpends`
(`src/policy/ephemeral_policy.cpp`) refusing a child that leaves the
dust unspent, and `PreCheckEphemeralTx` a parent with dust that pays a
fee or carries more than one dust output. A block disconnected in a
reorg returns such a parent to the mempool with no spend of its dust
checked.

Two rules are newer than a bitcoind before `v31.0`, whose first tag,
`v31.0rc1`, is the first to carry both, neither changing an RPC: so
the two bodies they decide read the build's `getnetworkinfo` `version`
against `_ZERO_FEE_REORG_VERSION`, and do as Core's `v30.3` file does before
it.
bitcoin/bitcoin#33892 lets a package pay for a parent below the relay
fee whatever its version, so a version 2 parent and its sweep are
refused with `min relay fee not met` before it (`test_non_truc`).
bitcoin/bitcoin#33616 stops a reorg checking that a child spends its
parent's dust, so before it a child leaving that dust unspent does not come
back (`test_reorgs`), and one spending it does. A known limit: a `master`
build from those merges (`ec4ff99a22`, `7c80301439`) up to the move to
`31.99` reports a `30.99` version with both rules, so those two bodies
fail against such a build.

Each of Core's subtests is a body of its own, in Core's order, over a
fresh pair of nodes whose coins its own `MiniWallet` (`mini_wallet.py`)
mines on the first, the second dialling the first, and every assertion
of Core's own is kept. Each asks for what it uses, in this order:
`Capability.PACKAGE_ACCEPTANCE`, for `submitpackage`, where it submits a
package; `Capability.ORPHANAGE` where a package reaches the second node
only as Core's 1p1c relay takes it; `Capability.MIN_RELAY_TX_FEE` where
it restarts under `-minrelaytxfee=0`; `Capability.CONNECT`, and
`Capability.DISCONNECT` for the reorg body; `Capability.GENERATE` where
Core's `generate` mines a block from the node's own mempool; and
`Capability.MINE`.

What differs from Core's file:

- Core's nodes carry each subtest's options on from the one before it;
  here each body's pair starts with the options Core's own nodes have
  where that subtest begins. Every start and restart here passes
  `-whitelist=noban,in,out@127.0.0.1`: Core's `noban_tx_relay` writes it
  into every node's `bitcoin.conf`, so Core's own restarts keep it
  whatever options they name;
- Core's `MiniWallet` spends the coins of the chain its framework
  caches, and its `rescan_utxos` finds more once a block confirms them;
  here each body's own mines, to maturity, the coins that body spends;
- Core's `generate` is `generatetoaddress` to Core's own first
  deterministic address, as `TestNode.generate` makes it; its
  `generateblock` is a block `build_next_block` (`mini_wallet.py`)
  builds client-side, submitted over `submitblock`, and its
  `create_empty_fork` is `build_fork`;
- Core's `add_output_to_create_multi_result` edits the dictionary its
  `create_self_transfer_multi` returns; `_add_output` edits the
  transaction itself, and `MiniWallet.new_utxos` reads the coins back off
  it;
- `_assert_mempool_contents` is Core's `assert_mempool_contents`
  (`test/functional/test_framework/mempool_util.py`), which this
  repository's `mempool_util.py` does not carry.

Each `mempool_ephemeral_dust_<node>_test.py` module beside this one runs
each body, `tests/integration/conftest.py`'s own module docstring having
how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, NamedTuple

import pytest
from bitcoin_core_rpc import RpcError, RPCErrorCode
from btclib.tx import TxOut
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet, build_fork, build_next_block
from btclib_functional_tests.node import (
    connect_nodes,
    disconnect_nodes,
    sync_all,
    wait_until_mempools_agree,
)
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.block.block import Block
    from btclib.tx import Tx

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.mini_wallet import Utxo
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_batch_sweep_must_spend_every_parents_dust",
    "a_childless_dusty_parent_stays_unmined",
    "a_dusty_parent_paying_a_fee_is_refused",
    "a_non_truc_dusty_parent_enters_with_its_spender",
    "a_parent_with_two_dust_outputs_is_refused",
    "a_reorg_returns_dust_to_the_mempool_unchecked",
    "a_restart_drops_an_ephemeral_package",
    "any_single_dust_output_is_allowed_alone",
    "dust_left_unspent_refuses_the_child",
    "zero_value_dust_enters_with_the_package_spending_it",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v31.0`, whose first tag,
# `v31.0rc1`, is the first to carry bitcoin/bitcoin#33892 and
# bitcoin/bitcoin#33616
_ZERO_FEE_REORG_VERSION = 310000

# what Core's own `noban_tx_relay` writes into every node's `bitcoin.conf`,
# which every start and restart reads
_NOBAN = "-whitelist=noban,in,out@127.0.0.1"

# what Core's own restarts of both nodes name, around the subtests
# needing no relay floor
_NO_RELAY_FLOOR = "-minrelaytxfee=0"

# Core's own first deterministic address, `TestNode.PRIV_KEYS`
# (`test_framework/test_node.py`), the one its `generate` pays
_ADDRESS = "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z"

# Core's own `COIN` (`test_framework/messages.py`)
_COIN = 100_000_000

# Core's own "dust threshold for taproot outputs", the output every coin
# of `MiniWallet` pays
_P2TR_DUST_THRESHOLD = 330

# Core's own `FORK_LENGTH` (`test_framework/blocktools.py`), the blocks
# `create_empty_fork` builds
_FORK_LENGTH = 10

# Core's own chain, `send_self_transfer_chain`'s, off the second sweep
_CHAIN_LENGTH = 10

# Core's own batch of dusty parents, swept by one child
_BATCH = 24

# Core's own `sync_mempools` and `sync_blocks` wait, in seconds
_WAIT = 60.0

_MIN_RELAY_FEE_NOT_MET = "min relay fee not met"
_DUST = "dust"
_DUST_MUST_BE_ZERO_FEE = "dust, tx with dust output must be 0-fee"
_PRIORITY_REFUSED = "Priority is not supported for transactions with dust outputs."


class _Package(NamedTuple):
    """Core's `create_ephemeral_dust_package`, and the dusty parent's fee."""

    dusty: Tx
    sweep: Tx
    fee: int


def _hex(tx: Tx) -> str:
    """Return `tx` serialized with its witness, the form every RPC takes."""
    return tx.serialize(True, check_validity=False).hex()


def _txid(tx: Tx) -> str:
    """Return `tx`'s own txid, as the node's RPC spells one."""
    return tx.id.hex()


def _wtxid(tx: Tx) -> str:
    """Return `tx`'s own wtxid, the key of `submitpackage`'s `tx-results`."""
    return tx.hash.hex()


def _fee(coins: Sequence[Utxo], tx: Tx) -> int:
    """Return what `tx`, spending `coins`, pays in fee, in satoshis."""
    return sum(coin.value for coin in coins) - sum(out.value for out in tx.vout)


def _missing_ephemeral_spends(tx: Tx) -> str:
    """Return Core's refusal of `tx` for leaving a parent's dust unspent."""
    return (
        f"missing-ephemeral-spends, tx {_txid(tx)} (wtxid={_wtxid(tx)}) "
        "did not spend parent's ephemeral dust"
    )


def _has_zero_fee_reorg_rules(node: NodeAdapter) -> bool:
    """Whether `node` has bitcoin/bitcoin#33892 and bitcoin/bitcoin#33616.

    Read off `bitcoind_version` against `_ZERO_FEE_REORG_VERSION`. Any node
    but a bitcoind answers yes.
    """
    version = bitcoind_version(node)
    return version is None or version >= _ZERO_FEE_REORG_VERSION


def _mempool(node: NodeAdapter) -> list[str]:
    """Return `node`'s own `getrawmempool`."""
    mempool = node.rpc.call("getrawmempool")
    assert isinstance(mempool, list), mempool
    return mempool


def _submit(node: NodeAdapter, package: Sequence[Tx]) -> dict[str, Any]:
    """Return `submitpackage`'s own answer for `package`."""
    result = node.rpc.call("submitpackage", [[_hex(tx) for tx in package]])
    assert isinstance(result, dict), result
    return result


def _test_accept(node: NodeAdapter, txs: Sequence[Tx]) -> list[dict[str, Any]]:
    """Return `testmempoolaccept`'s own answer for `txs`."""
    result = node.rpc.call("testmempoolaccept", [[_hex(tx) for tx in txs]])
    assert isinstance(result, list), result
    return result


def _assert_rpc_error(
    node: NodeAdapter, code: int, message: str, method: str, params: list[object]
) -> None:
    """Core's `assert_raises_rpc_error`: the code, and the message within."""
    with pytest.raises(RpcError) as refused:
        node.rpc.call(method, params)
    assert refused.value.code == code
    assert message in refused.value.args[0]


def _assert_mempool_contents(
    nodes: Sequence[NodeAdapter], expected: Sequence[Tx], *, sync: bool = True
) -> None:
    """Assert the first node's mempool holds `expected` and nothing else.

    Core's own `assert_mempool_contents`
    (`test/functional/test_framework/mempool_util.py`), always called on
    Core's first node: where `sync`, every node's mempool comes to agree
    first, Core's `sync_mempools`; then no transaction is listed twice,
    and the mempool is as long as `expected` and holds each of it.
    """
    if sync:
        wait_until_mempools_agree(nodes, timeout=_WAIT)
    txids = [_txid(tx) for tx in expected]
    assert len(txids) == len(set(txids)), txids
    mempool = _mempool(nodes[0])
    assert len(mempool) == len(txids), (mempool, txids)
    for txid in txids:
        assert txid in mempool, (txid, mempool)


def _add_output(tx: Tx, value: int = 0) -> None:
    """Add an output of `value`, keeping the fee: Core's own helper.

    `add_output_to_create_multi_result`: the new output pays the first
    output's own script, and the first output gives up `value`.
    """
    assert tx.vout, tx
    first = tx.vout[0]
    assert first.value >= value, (first.value, value)
    tx.vout.append(TxOut(value, first.script_pub_key))
    tx.vout[0] = TxOut(first.value - value, first.script_pub_key)


def _package(
    wallet: MiniWallet,
    *,
    version: int,
    dust_tx_fee: int = 0,
    dust_value: int = 0,
    num_dust_outputs: int = 1,
    extra_sponsors: Sequence[Utxo] = (),
) -> _Package:
    """Return a dusty parent and a child spending every output of it.

    Core's own `create_ephemeral_dust_package`: the parent pays
    `dust_tx_fee` and carries `num_dust_outputs` outputs of `dust_value`
    beside its first; the child spends each of them and `extra_sponsors`,
    at `create_self_transfer_multi`'s own fee.
    """
    coin = wallet.get_utxo()
    dusty = wallet.create_self_transfer_multi(
        utxos_to_spend=[coin], fee_per_output=dust_tx_fee, version=version
    )
    for _ in range(num_dust_outputs):
        _add_output(dusty, dust_value)
    sweep = wallet.create_self_transfer_multi(
        utxos_to_spend=[*wallet.new_utxos(dusty), *extra_sponsors], version=version
    )
    return _Package(dusty, sweep, _fee([coin], dusty))


def _pair(
    cluster: _Cluster,
    skip_counts: SkipCounts,
    *capabilities: Capability,
    coins: int,
    extra_args: Sequence[str] = (),
) -> tuple[Sequence[NodeAdapter], MiniWallet]:
    """Return a fresh pair of nodes, linked, and a wallet of `coins` coins.

    :param capabilities: what the body asks for, in order.
    :param coins: how many coinbases have matured once the wallet mines.
    :param extra_args: the options both nodes restart with before they
        are linked, where given.
    """
    nodes = cluster(2)
    for capability in capabilities:
        require(capability, nodes[0].capabilities, skip_counts)
    if extra_args:
        for node in nodes:
            node.restart(extra_args)
    connect_nodes(nodes[1], nodes[0])
    wallet = MiniWallet(nodes[0])
    wallet.generate(COINBASE_MATURITY + coins)
    sync_all(nodes, timeout=_WAIT)
    return nodes, wallet


def _restart(nodes: Sequence[NodeAdapter], extra_args: Sequence[str]) -> None:
    """Restart both nodes with `extra_args`; the first then dials the second."""
    for node in nodes:
        node.restart(extra_args)
    connect_nodes(nodes[0], nodes[1])


def _generate(nodes: Sequence[NodeAdapter], wallet: MiniWallet) -> None:
    """Core's `generate`: the first node mines a block, and every node syncs."""
    nodes[0].rpc.call("generatetoaddress", [1, _ADDRESS])
    sync_all(nodes, timeout=_WAIT)
    wallet.resync()


def _generateblock(node: NodeAdapter, wallet: MiniWallet, txs: Sequence[Tx]) -> None:
    """Core's `generateblock` to the wallet's address, carrying `txs`."""
    block = build_next_block(node, wallet.script_pub_key, txs)
    answer = node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    assert answer is None, answer
    wallet.resync()


def _trigger_reorg(
    node: NodeAdapter, wallet: MiniWallet, fork: Sequence[Block]
) -> None:
    """Core's own `trigger_reorg`: submit `fork`, which becomes the chain."""
    for block in fork:
        node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    assert node.rpc.call("getbestblockhash") == fork[-1].header.hash.hex()
    wallet.resync()


def _prioritise(node: NodeAdapter, tx: Tx, fee_delta: int) -> None:
    """Core's `prioritisetransaction(txid=..., dummy=0, fee_delta=...)`."""
    node.rpc.call("prioritisetransaction", [_txid(tx), 0, fee_delta])


def zero_value_dust_enters_with_the_package_spending_it(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a zero-fee TRUC parent's dust enters with the child spending it.

    Core's `test_normal_dust`: alone, or tested as a package, the parent
    pays too little, and a fee delta taking it past that leaves it refused
    as dust; submitted as a package it enters, and reaches the peer. Once
    it is in the mempool no delta may be set on it, and a block takes both.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.ORPHANAGE,
        Capability.CONNECT,
        Capability.GENERATE,
        Capability.MINE,
        coins=1,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    assert _mempool(node) == []
    dusty, sweep, _ = _package(wallet, version=3)

    test_res = _test_accept(node, [dusty, sweep])
    assert not test_res[0]["allowed"], test_res
    assert test_res[0]["reject-reason"] == _MIN_RELAY_FEE_NOT_MET, test_res
    _assert_rpc_error(
        node,
        RPCErrorCode.VERIFY_REJECTED,
        _MIN_RELAY_FEE_NOT_MET,
        "sendrawtransaction",
        [_hex(dusty)],
    )

    _prioritise(node, dusty, _COIN)
    test_res = _test_accept(node, [dusty])
    assert not test_res[0]["allowed"], test_res
    assert test_res[0]["reject-reason"] == _DUST, test_res
    _prioritise(node, dusty, -_COIN)
    assert node.rpc.call("getprioritisedtransactions") == {}

    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "success", res
    _assert_mempool_contents(nodes, [dusty, sweep])

    _assert_rpc_error(
        node,
        RPCErrorCode.INVALID_PARAMETER,
        _PRIORITY_REFUSED,
        "prioritisetransaction",
        [_txid(dusty), 0, 1],
    )
    assert node.rpc.call("getprioritisedtransactions") == {}

    _generate(nodes, wallet)
    assert _mempool(node) == []


def a_childless_dusty_parent_stays_unmined(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a dusty parent whose child is replaced stays, and is not mined.

    Core's `test_sponsor_cycle`: a package whose child also spends a
    sponsoring coin enters; a transaction spending that coin alone
    replaces the child, and the parent stays in the mempool with no
    descendant fee. A block does not take it; a new child spending its
    dust enters alone, and a block takes both.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.ORPHANAGE,
        Capability.CONNECT,
        Capability.GENERATE,
        Capability.MINE,
        coins=2,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    assert _mempool(node) == []
    sponsor_coin = wallet.get_utxo()
    dusty, sweep, _ = _package(wallet, version=3, extra_sponsors=[sponsor_coin])

    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "success", res
    assert len(_mempool(node)) == 2
    _assert_mempool_contents(nodes, [dusty, sweep])

    unsponsor = wallet.create_self_transfer_multi(
        utxos_to_spend=[sponsor_coin], num_outputs=1, fee_per_output=2000, version=3
    )
    node.rpc.call("sendrawtransaction", [_hex(unsponsor)])

    entry_info = node.rpc.call("getmempoolentry", [_txid(dusty)])
    assert entry_info["descendantcount"] == 1, entry_info
    assert entry_info["fees"]["descendant"] == 0, entry_info

    _assert_mempool_contents(nodes, [dusty, unsponsor])

    _generate(nodes, wallet)
    _assert_mempool_contents(nodes, [dusty])

    sweep = wallet.create_self_transfer_multi(
        utxos_to_spend=wallet.new_utxos(dusty), version=3
    )
    node.rpc.call("sendrawtransaction", [_hex(sweep)])
    _assert_mempool_contents(nodes, [dusty, sweep])

    _generate(nodes, wallet)
    _assert_mempool_contents(nodes, [])


def a_restart_drops_an_ephemeral_package(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a restart drops an ephemeral package from both mempools.

    Core's `test_node_restart`: a package in both mempools does not come
    back after both nodes restart, the parent reloaded alone paying too
    little.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.ORPHANAGE,
        Capability.CONNECT,
        Capability.MINE,
        coins=1,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    assert _mempool(node) == []
    dusty, sweep, _ = _package(wallet, version=3)

    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "success", res
    assert len(_mempool(node)) == 2
    _assert_mempool_contents(nodes, [dusty, sweep])

    _restart(nodes, [_NOBAN])
    _assert_mempool_contents(nodes, [])


def a_dusty_parent_paying_a_fee_is_refused(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a parent with dust must pay no fee, whatever its fee delta.

    Core's `test_fee_having_parent`: a package whose parent pays a satoshi
    is refused, and still is once a delta takes its modified fee to zero;
    a zero-fee parent given a positive delta is refused alone and as a
    package.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.CONNECT,
        Capability.MINE,
        coins=2,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    assert _mempool(node) == []

    sats_fee = 1
    dusty, sweep, fee = _package(wallet, version=3, dust_tx_fee=sats_fee)
    assert fee == sats_fee
    assert dusty.vout[0].value > _P2TR_DUST_THRESHOLD
    assert dusty.vout[1].value == 0

    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "transaction failed", res
    assert res["tx-results"][_wtxid(dusty)]["error"] == _DUST_MUST_BE_ZERO_FEE, res

    for each in nodes:
        _prioritise(each, dusty, -sats_fee)
    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "transaction failed", res
    assert res["tx-results"][_wtxid(dusty)]["error"] == _DUST_MUST_BE_ZERO_FEE, res

    dusty, sweep, _ = _package(wallet, version=3)
    for each in nodes:
        _prioritise(each, dusty, 1000)

    test_res = _test_accept(node, [dusty])
    assert not test_res[0]["allowed"], test_res
    assert test_res[0]["reject-reason"] == _DUST, test_res

    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "transaction failed", res
    assert res["tx-results"][_wtxid(dusty)]["error"] == _DUST_MUST_BE_ZERO_FEE, res

    _assert_mempool_contents(nodes, [])


def a_parent_with_two_dust_outputs_is_refused(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a parent with a second dust output is refused as dust.

    Core's `test_multidust`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.CONNECT,
        Capability.MINE,
        coins=1,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    _assert_mempool_contents(nodes, [])
    dusty, sweep, _ = _package(wallet, version=3, num_dust_outputs=2)

    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "transaction failed", res
    assert res["tx-results"][_wtxid(dusty)]["error"] == _DUST, res
    assert _mempool(node) == []


def any_single_dust_output_is_allowed_alone(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a zero-fee parent with one output of any value is allowed alone.

    Core's `test_nonzero_dust`: with no relay floor, `testmempoolaccept`
    allows a parent whose one dust output holds a satoshi, a satoshi
    short of the P2TR dust threshold, or that threshold, its spend
    unchecked; restarted without `-minrelaytxfee=0`, neither node holds
    anything.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.MIN_RELAY_TX_FEE,
        Capability.CONNECT,
        Capability.MINE,
        coins=3,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    _restart(nodes, [_NOBAN, _NO_RELAY_FLOOR])
    for value in (1, _P2TR_DUST_THRESHOLD - 1, _P2TR_DUST_THRESHOLD):
        assert _mempool(node) == []
        dusty, _, _ = _package(wallet, version=3, dust_value=value)
        test_res = _test_accept(node, [dusty])
        assert test_res[0]["allowed"], (value, test_res)

    _restart(nodes, [_NOBAN])
    _assert_mempool_contents(nodes, [])


def a_non_truc_dusty_parent_enters_with_its_spender(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a version 2 parent's dust enters with the child spending it.

    Core's `test_non_truc`. Before `v31.0` the package is refused whole,
    for the parent's fee.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.ORPHANAGE,
        Capability.CONNECT,
        Capability.GENERATE,
        Capability.MINE,
        coins=1,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    assert _mempool(node) == []
    dusty, sweep, _ = _package(wallet, version=2)

    res = _submit(node, [dusty, sweep])
    if not _has_zero_fee_reorg_rules(node):
        assert res["package_msg"] == "transaction failed", res
        error = res["tx-results"][_wtxid(dusty)]["error"]
        assert error.startswith(f"{_MIN_RELAY_FEE_NOT_MET}, 0 < "), res
        assert _mempool(node) == []
        return
    assert res["package_msg"] == "success", res
    _assert_mempool_contents(nodes, [dusty, sweep])
    _generate(nodes, wallet)


def dust_left_unspent_refuses_the_child(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a child leaving its parent's dust unspent is refused.

    Core's `test_unspent_ephemeral`: with the package in the mempool, a
    child paying more but spending the parent's other output alone is
    refused, as a package and alone, and one spending both enters. With
    neither in the mempool, a package whose child leaves the dust unspent
    is refused whole, and one whose child spends the dust alone, and a
    coin of its own, enters.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.ORPHANAGE,
        Capability.CONNECT,
        Capability.GENERATE,
        Capability.MINE,
        coins=3,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    assert _mempool(node) == []
    dusty, sweep, _ = _package(wallet, version=3, dust_value=_P2TR_DUST_THRESHOLD - 1)
    dusty_coins = wallet.new_utxos(dusty)

    _submit(node, [dusty, sweep])
    _assert_mempool_contents(nodes, [dusty, sweep])

    unspent_sweep = wallet.create_self_transfer_multi(
        fee_per_output=2000, utxos_to_spend=[dusty_coins[0]], version=3
    )
    assert _fee(dusty_coins[:1], unspent_sweep) > _fee(dusty_coins, sweep)
    res = _submit(node, [dusty, unspent_sweep])
    error = _missing_ephemeral_spends(unspent_sweep)
    assert res["tx-results"][_wtxid(unspent_sweep)]["error"] == error, res
    _assert_rpc_error(
        node,
        RPCErrorCode.VERIFY_REJECTED,
        error,
        "sendrawtransaction",
        [_hex(unspent_sweep)],
    )
    _assert_mempool_contents(nodes, [dusty, sweep])

    sweep_2 = wallet.create_self_transfer_multi(
        fee_per_output=2000, utxos_to_spend=dusty_coins, version=3
    )
    assert _hex(sweep) != _hex(sweep_2)
    res = _submit(node, [dusty, sweep_2])
    assert res["package_msg"] == "success", res

    _generate(nodes, wallet)
    assert _mempool(node) == []

    dusty, _, _ = _package(wallet, version=3, dust_value=_P2TR_DUST_THRESHOLD - 1)
    dusty_coins = wallet.new_utxos(dusty)

    unspent_sweep = wallet.create_self_transfer_multi(
        utxos_to_spend=[dusty_coins[0]], version=3
    )
    res = _submit(node, [dusty, unspent_sweep])
    assert res["package_msg"] == "unspent-dust", res
    assert _mempool(node) == []

    second_coin = wallet.get_utxo()
    sweep = wallet.create_self_transfer_multi(
        utxos_to_spend=[dusty_coins[1], second_coin], version=3
    )
    res = _submit(node, [dusty, sweep])
    assert res["package_msg"] == "success", res
    _assert_mempool_contents(nodes, [dusty, sweep])

    _generate(nodes, wallet)
    _assert_mempool_contents(nodes, [])


def a_reorg_returns_dust_to_the_mempool_unchecked(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a reorg returns dusty transactions to the mempool unchecked.

    Core's `test_reorgs`, on the first node with the second unlinked: a
    zero-fee parent the mempool refuses, mined and disconnected, is back
    in the mempool. From `v31.0` so is a zero-fee child leaving that dust
    unspent and carrying dust of its own, and with the parent mined, a
    child spending its dust and a chain off that child enter, the parent's
    return leaving every one of them in place; before it, that child does
    not come back and one spending the dust does, a child of that one
    refused as a `TRUC-violation`. A parent with two dust outputs, or with
    a fee, does not come back. Linked again, both nodes agree.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.CONNECT,
        Capability.DISCONNECT,
        Capability.MINE,
        coins=4,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    assert _mempool(node) == []
    disconnect_nodes(nodes[1], nodes[0])

    fork = build_fork(node, wallet.script_pub_key, _FORK_LENGTH)
    dusty, _, _ = _package(wallet, version=3)
    _assert_rpc_error(
        node,
        RPCErrorCode.VERIFY_REJECTED,
        _MIN_RELAY_FEE_NOT_MET,
        "sendrawtransaction",
        [_hex(dusty)],
    )
    _generateblock(node, wallet, [dusty])
    _trigger_reorg(node, wallet, fork)
    _assert_mempool_contents(nodes, [dusty], sync=False)

    dusty_coins = wallet.new_utxos(dusty)
    sweep = wallet.create_self_transfer_multi(
        fee_per_output=0, utxos_to_spend=[dusty_coins[0]], version=3
    )
    _add_output(sweep)
    _assert_rpc_error(
        node,
        RPCErrorCode.VERIFY_REJECTED,
        _MIN_RELAY_FEE_NOT_MET,
        "sendrawtransaction",
        [_hex(sweep)],
    )

    fork = build_fork(node, wallet.script_pub_key, _FORK_LENGTH)
    _generateblock(node, wallet, [dusty, sweep])
    _trigger_reorg(node, wallet, fork)
    if _has_zero_fee_reorg_rules(node):
        _assert_mempool_contents(nodes, [dusty, sweep], sync=False)
        _reorged_parent_keeps_its_descendants(nodes, wallet, dusty, dusty_coins, sweep)
    else:
        _reorged_child_is_checked_for_dust_spends(nodes, wallet, dusty, dusty_coins)

    multi_dusty, _, _ = _package(wallet, version=3, num_dust_outputs=2)
    fork = build_fork(node, wallet.script_pub_key, _FORK_LENGTH)
    _generateblock(node, wallet, [multi_dusty])
    _trigger_reorg(node, wallet, fork)
    assert _mempool(node) == []

    dusty_fee, _, _ = _package(wallet, version=3, dust_tx_fee=1)
    fork = build_fork(node, wallet.script_pub_key, _FORK_LENGTH)
    _generateblock(node, wallet, [dusty_fee])
    _trigger_reorg(node, wallet, fork)
    assert _mempool(node) == []

    connect_nodes(nodes[0], nodes[1])
    sync_all(nodes, timeout=_WAIT)


def _reorged_parent_keeps_its_descendants(
    nodes: Sequence[NodeAdapter],
    wallet: MiniWallet,
    dusty: Tx,
    dusty_coins: Sequence[Utxo],
    sweep: Tx,
) -> None:
    """`test_reorgs` from the parent's return, with bitcoin/bitcoin#33616.

    With the parent mined, a child spending its dust and a chain off that
    child enter, and the parent's return leaves every one of them in
    place.
    """
    node = nodes[0]
    fork = build_fork(node, wallet.script_pub_key, _FORK_LENGTH)
    _generateblock(node, wallet, [dusty])
    utxo = wallet.get_utxo()
    second_sweep = wallet.send_self_transfer_multi(
        utxos_to_spend=[dusty_coins[1], utxo], version=2
    )
    child_chain = wallet.send_self_transfer_chain(
        chain_length=_CHAIN_LENGTH, utxo_to_spend=wallet.new_utxos(second_sweep)[0]
    )

    expected_pool = [sweep, second_sweep, *child_chain]
    _assert_mempool_contents(nodes, expected_pool, sync=False)

    expected_pool = [dusty, *expected_pool]
    _trigger_reorg(node, wallet, fork)
    _assert_mempool_contents(nodes, expected_pool, sync=False)

    _generateblock(node, wallet, expected_pool)
    assert _mempool(node) == []


def _reorged_child_is_checked_for_dust_spends(
    nodes: Sequence[NodeAdapter],
    wallet: MiniWallet,
    dusty: Tx,
    dusty_coins: Sequence[Utxo],
) -> None:
    """Core's `v30.3` `test_reorgs`, from the first sweep's return on.

    The sweep leaving the parent's dust unspent does not come back with
    it. One spending the dust does, and a child of that one is refused as
    a `TRUC-violation`.
    """
    node = nodes[0]
    _assert_mempool_contents(nodes, [dusty], sync=False)

    sweep_2 = wallet.create_self_transfer_multi(
        fee_per_output=0, utxos_to_spend=dusty_coins, version=3
    )
    _add_output(sweep_2)
    _assert_rpc_error(
        node,
        RPCErrorCode.VERIFY_REJECTED,
        _MIN_RELAY_FEE_NOT_MET,
        "sendrawtransaction",
        [_hex(sweep_2)],
    )

    fork = build_fork(node, wallet.script_pub_key, _FORK_LENGTH)
    _generateblock(node, wallet, [dusty, sweep_2])
    _trigger_reorg(node, wallet, fork)
    _assert_mempool_contents(nodes, [dusty, sweep_2], sync=False)

    child = wallet.create_self_transfer_multi(
        utxos_to_spend=wallet.new_utxos(sweep_2), version=3
    )
    _assert_rpc_error(
        node,
        RPCErrorCode.VERIFY_REJECTED,
        "TRUC-violation",
        "sendrawtransaction",
        [_hex(child)],
    )

    _generateblock(node, wallet, [dusty, sweep_2])
    assert _mempool(node) == []


def a_batch_sweep_must_spend_every_parents_dust(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check a child of many dusty parents must spend the dust of each.

    Core's `test_no_minrelay_fee`, with no relay floor: a version 2 parent
    and its child enter. Of a batch of zero-fee parents, each with one
    dust output, a child spending all but one of their outputs is refused
    and every parent enters; with the parents in the mempool, the child
    is refused again beside another spending all but the last parent's
    outputs. Once a transaction spending that other child's own coin
    replaces it, a child spending every output of the batch enters.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes, wallet = _pair(
        cluster,
        skip_counts,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.MIN_RELAY_TX_FEE,
        Capability.CONNECT,
        Capability.GENERATE,
        Capability.MINE,
        coins=_BATCH + 2,
        extra_args=[_NOBAN],
    )
    node = nodes[0]

    _restart(nodes, [_NOBAN, _NO_RELAY_FLOOR])
    assert _mempool(node) == []
    dusty, sweep, _ = _package(wallet, version=2)
    _submit(node, [dusty, sweep])
    _assert_mempool_contents(nodes, [dusty, sweep])

    _generate(nodes, wallet)
    assert _mempool(node) == []

    dusty_txs = []
    for _ in range(_BATCH):
        dusty_txs.append(wallet.create_self_transfer_multi(fee_per_output=0, version=2))
        _add_output(dusty_txs[-1])
    all_parent_utxos = [utxo for tx in dusty_txs for utxo in wallet.new_utxos(tx)]

    insufficient_sweep = wallet.create_self_transfer_multi(
        fee_per_output=25_000, utxos_to_spend=all_parent_utxos[:-1], version=2
    )
    error = _missing_ephemeral_spends(insufficient_sweep)

    res = _submit(node, [*dusty_txs, insufficient_sweep])
    assert res["package_msg"] == "transaction failed", res
    assert res["tx-results"][_wtxid(insufficient_sweep)]["error"] == error, res
    _assert_mempool_contents(nodes, dusty_txs)

    b_coin = wallet.get_utxo()
    sweep_all_but_one = wallet.create_self_transfer_multi(
        fee_per_output=20_000,
        utxos_to_spend=[*all_parent_utxos[:-2], b_coin],
        version=2,
    )
    res = _submit(node, [*dusty_txs[:-1], sweep_all_but_one])
    assert res["package_msg"] == "success", res
    _assert_mempool_contents(nodes, [*dusty_txs, sweep_all_but_one])

    res = _submit(node, [*dusty_txs, insufficient_sweep])
    assert res["package_msg"] == "transaction failed", res
    assert res["tx-results"][_wtxid(insufficient_sweep)]["error"] == error, res
    _assert_mempool_contents(nodes, [*dusty_txs, sweep_all_but_one])

    cancel_sweep = wallet.create_self_transfer_multi(
        fee_per_output=21_000, utxos_to_spend=[b_coin], version=2
    )
    node.rpc.call("sendrawtransaction", [_hex(cancel_sweep)])
    _assert_mempool_contents(nodes, [*dusty_txs, cancel_sweep])

    sweep = wallet.create_self_transfer_multi(
        fee_per_output=25_000, utxos_to_spend=all_parent_utxos, version=2
    )
    res = _submit(node, [*dusty_txs, sweep])
    assert res["package_msg"] == "success", res
    _assert_mempool_contents(nodes, [*dusty_txs, sweep, cancel_sweep])

    _generate(nodes, wallet)
    assert _mempool(node) == []

    _restart(nodes, [_NOBAN])
    assert _mempool(node) == []
