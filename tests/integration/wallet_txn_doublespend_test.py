# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_txn_doublespend`, one body over each wallet.

Read from Core's `test/functional/wallet_txn_doublespend.py`
(`3fd68a95e68b`, 2026-04-07): two payments of node 0's wallet, made
while node 2 is split off, are conflicted by a double spend node 2
mines, and the balances of the payer and the payee follow. Core runs it
twice, the second time with `--mineblock`, where node 0 first confirms
the two payments and the reorganization takes them out of its block.
Every assertion of Core's own is kept.

The body reaches its wallets only through `wallet.WalletAdapter`, made
by the `wallet.WalletFactory` its module hands it. Each node is asked
for the factory's node capabilities first, then for
`Capability.GENERATE`, `Capability.CONNECT` and `Capability.DISCONNECT`.

Core's chain is the one its harness caches; `cached_chain` mines it
here, to the wallets' addresses. Core's `fundrawtransaction` in
`spend_utxo` is spelled out: the change and a fixed fee, which the body
reads back. A fee here is what a transaction pays, where Core's
`gettransaction` answers it negative.

Each `wallet_txn_doublespend_<node>_test.py` module beside this one runs
it, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from btclib.amount import sats_from_btc
from btclib.script.script_pub_key import ScriptPubKey, address
from btclib.tx import OutPoint, Tx, TxIn, TxOut

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import (
    connect_nodes,
    disconnect_nodes,
    sync_all,
    wait_until_tips_agree,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import TxInfo, WalletAdapter, WalletFactory

__all__ = [
    "UNSPENDABLE",
    "a_double_spend_conflicts_the_wallet_s_payments",
    "cached_chain",
    "outpoint_paying",
    "require_wallet_nodes",
]

# Core's harness's own `default_wallet_name` (`test_framework.py`)
_WALLET = "default_wallet"

# Core's `COIN` (`test_framework/messages.py`)
_COIN = 100_000_000

# Core's `ADDRESS_BCRT1_UNSPENDABLE` (`test_framework/address.py`): a
# witness v0 program of 32 zero bytes, which no key spends
UNSPENDABLE = address(b"\x00\x20" + bytes(32), "regtest")

# the fee of each payment `_spend_utxo` makes, where Core's
# `fundrawtransaction` chooses one
_SPEND_FEE = 10_000


def _sats(btc: str) -> int:
    """Return a bitcoin amount Core's test writes as a `Decimal`, in satoshi."""
    return sats_from_btc(Decimal(btc))


def _to(addr: str) -> ScriptPubKey:
    """Return the script `addr` pays to."""
    return ScriptPubKey.from_address(addr)


def cached_chain(node: NodeAdapter, addresses: Sequence[str]) -> None:
    """Mine Core's cached chain on `node`, and the block its setup adds.

    Core's `_initialize_chain` (`test_framework.py`) mines eight rounds
    of 25 blocks, the last of 24, to four addresses in turn, and
    `setup_nodes` then mines one block to node 0's: at that tip each of
    the first three addresses holds 25 mature coinbases. `addresses`
    are the four, node 0's first; where a test starts fewer nodes, the
    address of each node it does not start is `UNSPENDABLE`.
    """
    rounds = 8
    for i in range(rounds):
        count = 25 if i != rounds - 1 else 24
        node.rpc.call("generatetoaddress", [count, addresses[i % len(addresses)]])
    node.rpc.call("generatetoaddress", [1, addresses[0]])


def require_wallet_nodes(
    node: NodeAdapter,
    wallets: WalletFactory,
    capabilities: Sequence[Capability],
    skip_counts: SkipCounts,
) -> None:
    """Ask `node` for the factory's node capabilities, then `capabilities`."""
    for capability in sorted(wallets.node_capabilities, key=lambda c: c.value):
        require(capability, node.capabilities, skip_counts)
    for capability in capabilities:
        require(capability, node.capabilities, skip_counts)


def outpoint_paying(tx: Tx, addr: str) -> OutPoint:
    """Core's `find_vout_for_address`: the output of `tx` paying `addr`."""
    script = _to(addr).script
    vout = next(
        i for i, tx_out in enumerate(tx.vout) if tx_out.script_pub_key.script == script
    )
    return OutPoint(tx.id, vout)


def _create_outpoint(wallet: WalletAdapter, addr: str, amount: int) -> OutPoint:
    """Core's `create_outpoints`, for one output: `send`, then its output."""
    return outpoint_paying(wallet.spend([TxOut(amount, _to(addr))]), addr)


def _spend_utxo(
    wallet: WalletAdapter, node: NodeAdapter, utxo: OutPoint, addr: str, amount: int
) -> bytes:
    """Core's `spend_utxo`: pay `amount` to `addr` from `utxo`, on `node`.

    Core's `fundrawtransaction` adds the change, which this pays to a
    change address of `wallet`, less `_SPEND_FEE`.
    """
    value = wallet.transaction(utxo.tx_id).tx.vout[utxo.vout].value
    change = TxOut(value - amount - _SPEND_FEE, _to(wallet.new_change_address()))
    tx = wallet.sign(Tx(2, 0, [TxIn(utxo)], [TxOut(amount, _to(addr)), change]))
    node.rpc.call("sendrawtransaction", [tx.serialize(include_witness=True).hex()])
    return tx.id


def _sent(info: TxInfo) -> int:
    """Return what a payment takes from its wallet: Core's `amount + fee`."""
    assert info.fee is not None
    return info.amount - info.fee


def a_double_spend_conflicts_the_wallet_s_payments(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    wallets: WalletFactory,
    skip_counts: SkipCounts,
    *,
    mine_block: bool,
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param wallets: the wallets under test.
    :param skip_counts: the session's own tally.
    :param mine_block: Core's `--mineblock`.
    """
    nodes = cluster(3)
    for node in nodes:
        require_wallet_nodes(
            node,
            wallets,
            (Capability.GENERATE, Capability.CONNECT, Capability.DISCONNECT),
            skip_counts,
        )
    node0, node1, node2 = nodes
    for node in nodes:
        node.restart(wallets.node_args)
    w0, w1, w2 = (wallets.create(node, _WALLET) for node in nodes)
    # the address Core's harness mines each node's blocks to
    a0, a1, a2 = (w.new_address() for w in (w0, w1, w2))
    cached_chain(node0, [a0, a1, a2, UNSPENDABLE])

    # Core's `setup_network`: each node dials the one before it, and the
    # test then splits node 2 off
    connect_nodes(node1, node0)
    connect_nodes(node2, node1)
    sync_all(nodes)
    disconnect_nodes(node2, node1)

    # each wallet holds 25 mature coinbases
    starting_balance = 1250 * _COIN

    for node in nodes:
        assert node.rpc.call("getblockchaininfo")["initialblockdownload"] is False

    for wallet in (w0, w1, w2):
        assert wallet.balance() == starting_balance

    # coins for foo and bar, foo's locked
    node0_address_foo = w0.new_address()
    fund_foo_utxo = _create_outpoint(w0, node0_address_foo, 1219 * _COIN)
    fund_foo_tx = w0.transaction(fund_foo_utxo.tx_id)
    w0.lock(fund_foo_utxo)

    node0_address_bar = w0.new_address()
    fund_bar_utxo = _create_outpoint(w0, node0_address_bar, 29 * _COIN)
    fund_bar_tx = w0.transaction(fund_bar_utxo.tx_id)

    assert fund_foo_tx.fee is not None
    assert fund_bar_tx.fee is not None
    assert w0.balance() == starting_balance - fund_foo_tx.fee - fund_bar_tx.fee

    node1_address = w1.new_address()

    # a payment of 1240 to node 1, signed and not broadcast
    doublespend_fee = _sats("0.02")
    change_address = w0.new_address()
    outputs = [
        TxOut(1240 * _COIN, _to(node1_address)),
        TxOut((1248 - 1240) * _COIN - doublespend_fee, _to(change_address)),
    ]
    doublespend = w0.sign(Tx(2, 0, [TxIn(fund_foo_utxo), TxIn(fund_bar_utxo)], outputs))

    # two payments, each spending one of the two coins
    txid1 = _spend_utxo(w0, node0, fund_foo_utxo, node1_address, 40 * _COIN)
    txid2 = _spend_utxo(w0, node0, fund_bar_utxo, node1_address, 20 * _COIN)

    if mine_block:
        node0.rpc.call("generatetoaddress", [1, a0])
        wait_until_tips_agree([node0, node1])

    tx1 = w0.transaction(txid1)
    tx2 = w0.transaction(txid2)

    # the starting balance, one more matured coinbase where node 0 mined,
    # less the two payments and every fee
    expected = starting_balance - fund_foo_tx.fee - fund_bar_tx.fee
    if mine_block:
        expected += 50 * _COIN
    expected += _sent(tx1) + _sent(tx2)
    assert w0.balance() == expected

    if mine_block:
        assert tx1.confirmations == 1
        assert tx2.confirmations == 1
        # node 1 holds both payments
        assert w1.balance() == starting_balance - tx1.amount - tx2.amount
    else:
        assert tx1.confirmations == 0
        assert tx2.confirmations == 0

    # node 2 mines the double spend and its parents
    for tx in (fund_foo_tx.tx, fund_bar_tx.tx, doublespend):
        node2.rpc.call("sendrawtransaction", [tx.serialize(include_witness=True).hex()])
    node2.rpc.call("generatetoaddress", [1, a2])

    # the network joins again, on node 2's chain
    connect_nodes(node1, node2)
    node2.rpc.call("generatetoaddress", [1, a2])
    sync_all(nodes)
    assert w0.transaction(doublespend.id).confirmations == 2

    tx1 = w0.transaction(txid1)
    tx2 = w0.transaction(txid2)

    # both payments are conflicted
    assert tx1.confirmations == -2
    assert tx2.confirmations == -2

    # two more matured coinbases, less the double spend and every fee
    expected = (
        starting_balance
        + 100 * _COIN
        - 1240 * _COIN
        - fund_foo_tx.fee
        - fund_bar_tx.fee
        - doublespend_fee
    )
    assert w0.balance() == expected

    # node 1 holds the double spend
    assert w1.balance() == starting_balance + 1240 * _COIN
