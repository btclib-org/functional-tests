# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_abandonconflict`, one body over each wallet.

Read from Core's `test/functional/wallet_abandonconflict.py`
(`fa6f7e36a8c2`, 2026-10-23): a chain of alice's payments drops out of
the mempool when node 0 restarts with a higher relay fee, is abandoned,
comes back in part, and is then conflicted by a double spend node 1
mines, on alice's side and on bob's, the payee's; a reorganization
takes the conflict back. Every assertion of Core's own is kept.

The body reaches its wallets only through `wallet.WalletAdapter`, made
by the `wallet.WalletFactory` its module hands it. Where Core asserts
the text of an error, or reads `listsinceblock` and `listtransactions`,
which list a transaction once per output, the body reads it through
`BitcoindWallet.rpc`, on bitcoind's wallet alone; through the adapter
it asserts the same of each transaction.

Each node is asked for the factory's node capabilities first, then for
`Capability.GENERATE`, `Capability.CONNECT`, `Capability.DISCONNECT` and
`Capability.INVALIDATE_BLOCK`, node 0 for `Capability.MIN_RELAY_TX_FEE`,
and the factory for `WalletCapability.RELOAD`, which unloading bob
needs. Core's `noban_tx_relay` is spelled out:
the noban permission at each start, and node 1 dialling node 0.

Core's chain is the one its harness caches,
`wallet_txn_doublespend_test.cached_chain`. Core asserts node 0's
mempool loaded once it restarts; this waits for it. Core's bob is not
loaded again when node 0 restarts, so the body unloads him before the
first restart: loaded, he would resubmit AB1, which pays him.

Each `wallet_abandonconflict_<node>_test.py` module beside this one runs
it, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from btclib.amount import sats_from_btc
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.bitcoind_wallet import BitcoindWallet
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import (
    connect_nodes,
    disconnect_nodes,
    sync_all,
    wait_until,
)
from btclib_functional_tests.wallet import WalletCapability
from tests.integration.wallet_signmessagewithaddress_test import refused
from tests.integration.wallet_txn_doublespend_test import (
    UNSPENDABLE,
    cached_chain,
    require_wallet_nodes,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import WalletAdapter, WalletFactory

__all__ = ["abandon_frees_the_inputs_and_a_conflict_marks_both_sides"]

# Core's harness's own `default_wallet_name` (`test_framework.py`)
_WALLET = "default_wallet"

# Core's `COIN` (`test_framework/messages.py`)
_COIN = 100_000_000

# what Core's own `noban_tx_relay` starts every node with
_NOBAN = "-whitelist=noban,in,out@127.0.0.1"

# node 0's relay fee: Core's, and the higher one its restarts set
_LOW_FEE = "-minrelaytxfee=0.00001"
_HIGH_FEE = "-minrelaytxfee=0.0001"

# `src/rpc/protocol.h`: `RPC_INVALID_ADDRESS_OR_KEY`, what
# `abandontransaction` answers a transaction it does not abandon
_RPC_INVALID_ADDRESS_OR_KEY = -5


def _sats(btc: str) -> int:
    """Return a bitcoin amount Core's test writes as a `Decimal`, in satoshi."""
    return sats_from_btc(Decimal(btc))


def _to(addr: str) -> ScriptPubKey:
    """Return the script `addr` pays to."""
    return ScriptPubKey.from_address(addr)


def _hex(tx: Tx) -> str:
    """Return `tx` as `sendrawtransaction` takes it."""
    return tx.serialize(include_witness=True).hex()


def _vout(wallet: WalletAdapter, txid: bytes, value: int) -> int:
    """Return the output of a wallet's transaction paying `value`.

    Core reads it from `gettransaction`'s `details`.
    """
    tx = wallet.transaction(txid).tx
    return next(i for i, tx_out in enumerate(tx.vout) if tx_out.value == value)


def _signed(
    wallet: WalletAdapter, inputs: Sequence[OutPoint], outputs: list[TxOut]
) -> Tx:
    """Core's `createrawtransaction`, then `signrawtransactionwithwallet`."""
    return wallet.sign(Tx(2, 0, [TxIn(outpoint) for outpoint in inputs], outputs))


def _refuses_abandon(wallet: WalletAdapter, txid: bytes, message: str) -> None:
    """Assert `wallet` refuses to abandon `txid`, as Core does on bitcoind."""
    with pytest.raises(ValueError, match=txid.hex()):
        wallet.abandon(txid)
    if isinstance(wallet, BitcoindWallet):
        refused(
            wallet.rpc,
            "abandontransaction",
            {"txid": txid.hex()},
            _RPC_INVALID_ADDRESS_OR_KEY,
            message,
        )


def _restart(node: NodeAdapter, fee: str, wallets: WalletFactory) -> None:
    """Restart node 0 with `fee`, and wait for its mempool to load."""
    node.restart([_NOBAN, fee, *wallets.node_args])
    wait_until(lambda: node.rpc.call("getmempoolinfo")["loaded"] is True)


def _check_listsinceblock(alice: WalletAdapter, txid: bytes) -> None:
    """Core's own check of an abandoned payment in `listsinceblock`."""
    if not isinstance(alice, BitcoindWallet):
        return
    answer = alice.rpc.call("listsinceblock")
    entries = [
        entry
        for entry in answer["transactions"]
        if entry["txid"] == txid.hex() and entry["category"] == "send"
    ]
    for entry in entries:
        assert entry["abandoned"] is True
        assert entry["confirmations"] == 0
        assert entry["trusted"] is False


def _check_listtransactions(
    alice: WalletAdapter, bob: WalletAdapter, tx_ab1: bytes, double_spend: bytes
) -> None:
    """Core's own checks of `listtransactions`, one entry per output."""
    if not isinstance(alice, BitcoindWallet) or not isinstance(bob, BitcoindWallet):
        return
    tx_list = alice.rpc.call("listtransactions")
    conflicted = [tx for tx in tx_list if tx["confirmations"] < 0]
    assert len(conflicted) == 4
    wallet_conflicts = [tx for tx in conflicted if tx["walletconflicts"]]
    assert len(wallet_conflicts) == 2
    double_spends = [
        tx for tx in tx_list if tx["walletconflicts"] and tx["confirmations"] > 0
    ]
    # one for each output
    assert len(double_spends) == 2
    entry = double_spends[0]
    for tx in conflicted:
        assert tx["abandoned"] is False
        assert tx["confirmations"] == -1
        assert tx["trusted"] is False
    assert entry["abandoned"] is False
    assert entry["confirmations"] == 1
    # `trusted` is answered only for 0 or fewer confirmations
    assert "trusted" not in entry
    for tx in wallet_conflicts:
        assert entry["walletconflicts"] == [tx["txid"]]
        assert tx["walletconflicts"] == [entry["txid"]]
    # the receiver's side
    double_spends = [
        tx
        for tx in bob.rpc.call("listtransactions")
        if tx["walletconflicts"] and tx["confirmations"] > 0
    ]
    assert len(double_spends) == 1
    assert double_spends[0]["txid"] == double_spend.hex()
    assert double_spends[0]["walletconflicts"] == [tx_ab1.hex()]


def abandon_frees_the_inputs_and_a_conflict_marks_both_sides(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    wallets: WalletFactory,
    skip_counts: SkipCounts,
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param wallets: the wallets under test.
    :param skip_counts: the session's own tally.
    """
    node0, node1 = nodes = cluster(2)
    for node in nodes:
        require_wallet_nodes(
            node,
            wallets,
            (
                Capability.GENERATE,
                Capability.CONNECT,
                Capability.DISCONNECT,
                Capability.INVALIDATE_BLOCK,
            ),
            skip_counts,
        )
    require(Capability.MIN_RELAY_TX_FEE, node0.capabilities, skip_counts)
    require(WalletCapability.RELOAD, wallets.capabilities, skip_counts)

    node0.restart([_NOBAN, _LOW_FEE, *wallets.node_args])
    node1.restart([_NOBAN, *wallets.node_args])

    # two wallets on node 0, to see a conflict from the sender's side and
    # from the receiver's
    alice = wallets.create(node0, _WALLET)
    bob = wallets.create(node0, "bob")
    w1 = wallets.create(node1, _WALLET)
    a0, a1 = alice.new_address(), w1.new_address()
    cached_chain(node0, [a0, a1, UNSPENDABLE, UNSPENDABLE])
    connect_nodes(node1, node0)
    sync_all(nodes)

    node1.rpc.call("generatetoaddress", [COINBASE_MATURITY, a1])
    sync_all(nodes)
    balance = alice.balance()
    tx_a = alice.spend([TxOut(10 * _COIN, _to(alice.new_address()))]).id
    tx_b = alice.spend([TxOut(10 * _COIN, _to(alice.new_address()))]).id
    tx_c = alice.spend([TxOut(10 * _COIN, _to(alice.new_address()))]).id
    sync_all(nodes)

    # a transaction in the mempool is not abandoned
    _refuses_abandon(alice, tx_a, "Transaction not eligible for abandonment")

    node1.rpc.call("generatetoaddress", [1, a1])
    sync_all(nodes)

    # nor is a transaction not the wallet's, or a confirmed one
    _refuses_abandon(
        alice, bytes.fromhex("ff" * 32), "Invalid or non-wallet transaction id"
    )
    _refuses_abandon(alice, tx_a, "Transaction not eligible for abandonment")

    newbalance = alice.balance()
    # no more than fees lost
    assert balance - newbalance < _sats("0.001")
    balance = newbalance

    # node 0's transactions stay out of node 1's mempool
    disconnect_nodes(node1, node0)

    n_a = _vout(alice, tx_a, 10 * _COIN)
    n_b = _vout(alice, tx_b, 10 * _COIN)
    n_c = _vout(alice, tx_c, 10 * _COIN)

    # spend the outputs of A and B
    signed = _signed(
        alice,
        [OutPoint(tx_a, n_a), OutPoint(tx_b, n_b)],
        [
            TxOut(_sats("14.99998"), _to(alice.new_address())),
            TxOut(5 * _COIN, _to(bob.new_address())),
        ],
    )
    node0.rpc.call("sendrawtransaction", [_hex(signed)])
    tx_ab1 = signed.id

    n_ab = _vout(alice, tx_ab1, _sats("14.99998"))

    # a child spending AB1 and C
    signed2 = _signed(
        alice,
        [OutPoint(tx_ab1, n_ab), OutPoint(tx_c, n_c)],
        [TxOut(_sats("24.9996"), _to(alice.new_address()))],
    )
    node0.rpc.call("sendrawtransaction", [_hex(signed2)])
    tx_abc2 = signed2.id

    # a child spending ABC2, only ever abandoned as its descendant
    signed3_change = _sats("24.999")
    signed3 = _signed(
        alice,
        [OutPoint(tx_abc2, 0)],
        [TxOut(signed3_change, _to(alice.new_address()))],
    )
    node0.rpc.call("sendrawtransaction", [_hex(signed3)])

    # the wallet's own transactions in the mempool add their change
    newbalance = alice.balance()
    assert newbalance == balance - 30 * _COIN + signed3_change
    balance = newbalance

    # the parent leaves the mempool: its fee is below the new relay fee
    bob.unload()
    _restart(node0, _HIGH_FEE, wallets)

    assert node0.rpc.call("getrawmempool") == []
    assert node1.rpc.call("getrawmempool") == []

    # outside the mempool they only spend: their inputs stay spent, and
    # their change is not received
    newbalance = alice.balance()
    assert newbalance == balance - signed3_change
    # bitcoind before v32.0 has no `nonmempool` bucket
    # (bitcoin/bitcoin#33671), and Core's own test there sums the other two
    # alone
    balances = alice.balances()
    nonmempool = 0 if balances.nonmempool is None else balances.nonmempool
    assert balances.untrusted_pending + balances.trusted + nonmempool == newbalance
    assert all(utxo.outpoint.tx_id != tx_abc2 for utxo in alice.utxos(0))
    balance = newbalance

    # abandoned, with its descendants: the inputs are free again
    alice.abandon(tx_ab1)
    newbalance = alice.balance()
    assert newbalance == balance + 30 * _COIN
    balance = newbalance

    info = alice.transaction(tx_ab1)
    assert info.abandoned is True
    assert info.confirmations == 0
    assert info.trusted is False
    _check_listsinceblock(alice, tx_ab1)

    # a lower relay fee does not take an abandoned transaction back
    _restart(node0, _LOW_FEE, wallets)

    assert node0.rpc.call("getrawmempool") == []
    assert alice.balance() == balance

    # received again, it is no longer abandoned, and its change counts;
    # its child stays abandoned
    node0.rpc.call("sendrawtransaction", [_hex(signed)])
    newbalance = alice.balance()
    assert newbalance == balance - 20 * _COIN + _sats("14.99998")
    balance = newbalance

    # and so is the child, sent again
    node0.rpc.call("sendrawtransaction", [_hex(signed2)])
    newbalance = alice.balance()
    expected = balance - 10 * _COIN - _sats("14.99998") + _sats("24.9996")
    assert newbalance == expected
    balance = newbalance

    # out of the mempool again
    _restart(node0, _HIGH_FEE, wallets)
    assert node0.rpc.call("getrawmempool") == []
    newbalance = alice.balance()
    assert newbalance == balance - _sats("24.9996")
    balance = newbalance

    # conflicts, from both sides
    bob.load()

    # a double spend of AB1, spending A's output alone, mined by node 1
    double = _signed(
        alice,
        [OutPoint(tx_a, n_a)],
        [
            TxOut(_sats("3.9999"), _to(w1.new_address())),
            TxOut(_sats("5.9999"), _to(bob.new_address())),
        ],
    )
    node1.rpc.call("sendrawtransaction", [_hex(double)])
    double_spend_txid = double.id
    connect_nodes(node0, node1)
    node1.rpc.call("generatetoaddress", [1, a1])
    sync_all(nodes)

    # AB1 and its two descendants are conflicted, and the double spend
    # conflicts with AB1
    for txid in (tx_ab1, tx_abc2, signed3.id):
        info = alice.transaction(txid)
        assert info.abandoned is False
        assert info.confirmations == -1
        assert info.trusted is False
    assert alice.transaction(tx_ab1).wallet_conflicts == {double_spend_txid}
    info = alice.transaction(double_spend_txid)
    assert info.abandoned is False
    assert info.confirmations == 1
    assert info.wallet_conflicts == {tx_ab1}
    _check_listtransactions(alice, bob, tx_ab1, double_spend_txid)

    # the receiver's side
    info = bob.transaction(tx_ab1)
    assert info.confirmations == -1
    assert info.wallet_conflicts == {double_spend_txid}
    assert bob.transaction(double_spend_txid).wallet_conflicts == {tx_ab1}

    # the outputs of B and C are free again, AB1 being conflicted
    assert alice.transaction(tx_ab1).confirmations == -1
    newbalance = alice.balance()
    assert newbalance == balance + 20 * _COIN
    balance = newbalance

    # the block of the double spend is invalidated, ten blocks under the
    # tip so that its transactions do not go back to the mempool: B's and
    # C's outputs are no longer free
    blk = node0.rpc.call("getbestblockhash")
    node1.rpc.call("generatetoaddress", [10, a1])
    sync_all(nodes)
    node0.rpc.call("invalidateblock", [blk])
    assert alice.transaction(tx_ab1).confirmations == 0
    newbalance = alice.balance()
    assert newbalance == balance - 20 * _COIN
