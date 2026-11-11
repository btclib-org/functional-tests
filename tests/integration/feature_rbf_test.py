# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_rbf`, one body over each node.

Read from Core's `test/functional/feature_rbf.py` (`1a85ca1dff1f`,
2026-04-24): a transaction spending what another in the mempool spends
replaces it where it pays for it, and is refused, with the reason Core's
file matches, where it does not -- less in all than what it replaces,
more by less than the incremental relay fee of its own size, a fee rate
that does not improve the mempool's feerate diagram, conflicts reaching
more clusters than Core's `MAX_REPLACEMENT_LIMIT`, or a spend of an output
of what it replaces. A fee changed by `prioritisetransaction` counts in
both the absolute check and the rate check, an input not signalling
BIP125 does not protect its transaction, and `-incrementalrelayfee` sets
what a replacement must add.

Core's `run_test` is the body, in its own order, over one node, every
assertion of Core's own kept. It asks for `Capability.INCREMENTAL_RELAY_FEE`,
`Capability.DATACARRIER`, `Capability.NODE_WALLET`, `Capability.GENERATE`
and `Capability.MINE`.

The replacement rules are of two generations, and the body reads which
one the node runs off its capabilities: a node declaring
`Capability.CLUSTER_REPLACEMENT` runs those of the cluster mempool
(bitcoin/bitcoin#33629, first in `v31.0`), any other the earlier ones.
Both refuse the same replacements but one, and say so in different words
where Core's file matches them:

- a replacement paying no more than what it replaces is refused for a
  feerate no higher, or for too few additional fees to relay;
- a replacement with many outputs and a lower fee rate is refused as
  `insufficient fee`, or as not improving the feerate diagram;
- a replacement adding an unconfirmed input is refused as
  `replacement-adds-unconfirmed`, or taken where it improves the diagram
  and refused as not doing so where it does not;
- a replacement reaching more than `MAX_REPLACEMENT_LIMIT` conflicts is
  refused for too many potential replacements, or for too many
  conflicting clusters.

What differs from Core's file:

- Core's second node is never read, so there is none;
- the chain Core's framework caches is mined here, every coinbase paying
  `MiniWallet` (`mini_wallet.py`), and Core's `generate` of the mempool is
  built client-side, carrying every transaction the mempool holds
  (`MiniWallet.generate`'s own `confirm`); a coin no block holds is then
  dropped from its cache, a replaced transaction's among them;
- Core's node wallet is funded by `generatetoaddress` to its own address
  before the first block `MiniWallet` mines, and is spent from by the
  `fundrawtransaction` calls of `test_rpc` alone;
- every fee and every amount Core writes in BTC is written here in
  satoshis, and a fee rate in satoshis per 1000 virtual bytes;
  `_multi` is Core's `create_self_transfer_multi(amount_per_output=...)`;
- Core's node starts with `-deprecatedrpc=fullrbf` and
  `-deprecatedrpc=bip125`, which only a build that has deprecated those
  keys needs, and, before the cluster mempool, with the ancestor and
  descendant limits Core's earlier file raised; both are given to the
  running node by one restart before anything is mined;
- `v29.4` refuses an `OP_RETURN` output larger than its default
  `-datacarriersize` with `scriptpubkey`, `v30.3` and later do not, and
  the padding Core's `target_vsize` adds is such an output, so every
  restart of this body carries the option (bitcoin/bitcoin#32406,
  first in `v30.0`);
- every build runs the file as pinned where Core's earlier file differs
  only in what it builds, each measured to leave the earlier rules their
  own verdict: a tree of `_DEFAULT_CLUSTER_LIMIT` transactions, an
  unconfirmed coin of `_UNCONFIRMED_COIN` satoshis, and
  the split transaction mined before its outputs are spent.

Each `feature_rbf_<node>_test.py` module beside this one runs the body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from bitcoin_core_rpc import RpcError, RPCErrorCode
from btclib.amount import sats_from_btc
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import Tx, TxOut
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.mini_wallet import Utxo
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["replace_by_fee_follows_its_rules"]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

# satoshis per bitcoin, Core's own `COIN` (`test_framework/messages.py`)
_COIN = 100_000_000

# Core's own `MAX_BIP125_RBF_SEQUENCE` (`test_framework/messages.py`): opts
# into BIP125 and out of BIP68
_MAX_BIP125_RBF_SEQUENCE = 0xFFFFFFFD

# Core's own `MAX_REPLACEMENT_LIMIT` (`feature_rbf.py`): how many conflicts
# one replacement may have
_MAX_REPLACEMENT_LIMIT = 100

# Core's own `DEFAULT_CLUSTER_LIMIT` (`test_framework/mempool_util.py`)
_DEFAULT_CLUSTER_LIMIT = 64

# Core's own `ADDRESS_BCRT1_UNSPENDABLE` (`test_framework/address.py`)
_UNSPENDABLE = "bcrt1qqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqqq3xueyj"

# Core's own `test_replacement_feeperkb` and `test_prioritised_transactions`
# per-output amount
_SMALL_OUTPUT = 1000

# Core's own `test_new_unconfirmed_inputs` value of the unconfirmed coin,
# `int(0.2 * COIN)`
_UNCONFIRMED_COIN = 20_000_000

# a `-datacarriersize` over the OP_RETURN padding of a 10000-vbyte
# transaction, which `v29.4` refuses by default
_DATACARRIER_SIZE = 10_000

# what Core's earlier `set_test_params` started the first node with, which
# the cluster mempool's own limits replace
_LEGACY_LIMITS = (
    "-limitancestorcount=50",
    "-limitancestorsize=101",
    "-limitdescendantcount=200",
    "-limitdescendantsize=101",
)

# Core's own `test_incremental_relay_feerates`: the settings it tries, in
# satoshis per kvB
_INCREMENTAL_SETTINGS = (0, 5, 10, 50, 100, 234, 1000, 5000, 21000)


@dataclass(frozen=True)
class _Rules:
    """The words, and the verdict, a build's replacement rules give.

    :param clustered: whether the node runs the cluster mempool's rules.
    """

    clustered: bool

    def same_fee_details(self, txid: str) -> str:
        """Return the details of a replacement paying what it replaces."""
        if self.clustered:
            return (
                f"insufficient fee, rejecting replacement {txid}, not enough "
                "additional fees to relay; 0.00 < 0.00000011"
            )
        return (
            f"insufficient fee, rejecting replacement {txid}; new feerate "
            "0.00300000 BTC/kvB <= old feerate 0.00300000 BTC/kvB"
        )

    @property
    def lower_feerate_refusal(self) -> str:
        """Return what a replacement of a lower fee rate is refused with."""
        if self.clustered:
            return "does not improve feerate diagram"
        return "insufficient fee"

    @property
    def adds_unconfirmed_refusal(self) -> str:
        """Return what a replacement adding an unconfirmed input breaks."""
        if self.clustered:
            return "insufficient feerate: does not improve feerate diagram"
        return "replacement-adds-unconfirmed"

    def too_many_details(self, txid: str) -> str:
        """Return the details of a replacement with too many conflicts."""
        count = f"({_MAX_REPLACEMENT_LIMIT + 1} > {_MAX_REPLACEMENT_LIMIT})"
        if self.clustered:
            return (
                f"too many potential replacements, rejecting replacement {txid}; "
                f"too many conflicting clusters {count}"
            )
        return (
            f"too many potential replacements, rejecting replacement {txid}; "
            f"too many potential replacements {count}"
        )


def _hex(tx: Tx) -> str:
    """Return `tx` serialized with its witness, the form every RPC takes."""
    return tx.serialize(True, check_validity=False).hex()


def _txid(tx: Tx) -> str:
    """Return `tx`'s own txid, as the node's RPC spells one."""
    return tx.id.hex()


def _send_params(tx: Tx, maxfeerate: int | None) -> list[object]:
    """Return `sendrawtransaction`'s params, `maxfeerate` where one is given."""
    params: list[object] = [_hex(tx)]
    if maxfeerate is not None:
        params.append(maxfeerate)
    return params


def _send(node: NodeAdapter, tx: Tx, maxfeerate: int | None = 0) -> str:
    """Send `tx` over `sendrawtransaction` and return the txid it answers.

    :param maxfeerate: Core's own second argument, `0` turning the fee-rate
        ceiling off, as every `sendrawtransaction(hex, 0)` of Core's file
        does; `None` leaves the node's default.
    """
    txid = node.rpc.call("sendrawtransaction", _send_params(tx, maxfeerate))
    assert txid == _txid(tx), txid
    return _txid(tx)


def _assert_refused(
    node: NodeAdapter, tx: Tx, message: str, *, maxfeerate: int | None = 0
) -> None:
    """Core's `assert_raises_rpc_error(-26, ...)` of `sendrawtransaction`."""
    with pytest.raises(RpcError) as refused:
        node.rpc.call("sendrawtransaction", _send_params(tx, maxfeerate))
    assert refused.value.code == RPCErrorCode.VERIFY_REJECTED
    assert message in refused.value.args[0], refused.value.args[0]


def _test_accept(node: NodeAdapter, tx: Tx) -> dict[str, Any]:
    """Return `testmempoolaccept`'s own verdict on `tx` alone."""
    result = node.rpc.call("testmempoolaccept", [[_hex(tx)]])
    assert isinstance(result, list), result
    verdict = result[0]
    assert isinstance(verdict, dict), verdict
    return verdict


def _assert_test_refused(node: NodeAdapter, tx: Tx, reason: str, details: str) -> None:
    """Assert `testmempoolaccept` refuses `tx` with `reason` and `details`."""
    verdict = _test_accept(node, tx)
    assert verdict["reject-reason"] == reason, verdict
    assert verdict["reject-details"] == details, verdict


def _mempool(node: NodeAdapter) -> list[str]:
    """Return the txids `getrawmempool` lists."""
    mempool = node.rpc.call("getrawmempool")
    assert isinstance(mempool, list), mempool
    return mempool


def _fee_of(tx: Tx, coins: Sequence[Utxo]) -> int:
    """Return what `tx`, spending `coins`, pays in fee, in satoshis."""
    return sum(coin.value for coin in coins) - sum(out.value for out in tx.vout)


def _set_value(tx: Tx, index: int, value: int) -> None:
    """Give `tx`'s output `index` the value `value`, its script as it is."""
    tx.vout[index] = TxOut(value, tx.vout[index].script_pub_key)


def _multi(
    wallet: MiniWallet,
    coins: Sequence[Utxo],
    amount_per_output: int,
    *,
    num_outputs: int = 1,
) -> Tx:
    """Return Core's `create_self_transfer_multi(amount_per_output=...)`.

    `MiniWallet.create_self_transfer_multi` takes a fee per output instead;
    the one that leaves each output `amount_per_output` is what the coins'
    own total, shared out, has over it.

    :param coins: the coins to spend, each with a zero sequence.
    :param amount_per_output: what each output is worth.
    :param num_outputs: how many outputs share the coins.
    """
    total = sum(coin.value for coin in coins)
    return wallet.create_self_transfer_multi(
        utxos_to_spend=coins,
        num_outputs=num_outputs,
        fee_per_output=total // num_outputs - amount_per_output,
    )


class _Rbf:
    """Core's `ReplaceByFeeTest`: the node, its wallet and its rules.

    :param node: the node under test.
    :param wallet: the wallet whose coins every body spends.
    :param rules: what the node's replacement rules say.
    """

    def __init__(self, node: NodeAdapter, wallet: MiniWallet, rules: _Rules) -> None:
        self.node = node
        self.wallet = wallet
        self.rules = rules

    def mine_mempool(self) -> None:
        """Core's `generate` of the mempool, then drop the coins no block holds.

        The block carries every transaction the mempool holds, a parent
        ahead of each child `getrawmempool`'s own `depends` names. With the
        mempool empty no transaction pays a coin of the cache any more
        that no block holds, a replaced transaction's among them.
        """
        entries = self.node.rpc.call("getrawmempool", [True])
        assert isinstance(entries, dict), entries
        ordered: list[str] = []
        while len(ordered) < len(entries):
            ordered += [
                txid
                for txid, entry in entries.items()
                if txid not in ordered and set(entry["depends"]) <= set(ordered)
            ]
        txs = [
            Tx.parse(
                bytes.fromhex(self.node.rpc.call("getrawtransaction", [txid])),
                check_validity=False,
            )
            for txid in ordered
        ]
        self.wallet.generate(1, confirm=txs)
        assert not _mempool(self.node)
        for utxo in self.wallet.get_utxos(
            mark_as_spent=False, include_immature_coinbase=True
        ):
            if not utxo.confirmed:
                self.wallet.get_utxo(
                    txid=utxo.outpoint.tx_id.hex(), vout=utxo.outpoint.vout
                )

    def make_utxo(self, amount: int, *, confirmed: bool = True) -> Utxo:
        """Return Core's `make_utxo`: a coin worth `amount`, mined or not.

        :param amount: the coin's value, in satoshis.
        :param confirmed: mine the mempool, the coin's transaction with it.
        """
        tx = self.wallet.send_to(self.wallet.script_pub_key, amount)
        if confirmed:
            self.mine_mempool()
        return self.wallet.get_utxo(txid=_txid(tx), vout=1)

    def simple_doublespend(self) -> None:
        """Core's `test_simple_doublespend`."""
        tx = self.wallet.create_self_transfer()
        tx_a_txid = _send(self.node, tx, None)

        # the same fee, a different txid: its last output script byte flipped
        script = tx.vout[0].script_pub_key.script
        flipped = ScriptPubKey(
            script[:-1] + bytes([script[-1] ^ 1]), check_validity=False
        )
        value = tx.vout[0].value
        tx.vout[0] = TxOut(value, flipped)
        details = self.rules.same_fee_details(_txid(tx))
        _assert_test_refused(self.node, tx, "insufficient fee", details)
        _assert_refused(self.node, tx, details)

        # an extra 0.1 BTC fee
        _set_value(tx, 0, value - _COIN // 10)
        tx_b_txid = _send(self.node, tx)
        mempool = _mempool(self.node)
        assert tx_a_txid not in mempool
        assert tx_b_txid in mempool
        assert self.node.rpc.call("getrawtransaction", [tx_b_txid]) == _hex(tx)

    def doublespend_chain(self) -> None:
        """Core's `test_doublespend_chain`."""
        initial = 5 * _COIN
        tx0 = self.make_utxo(initial)
        prevout = tx0
        remaining = initial
        chain_txids = []
        for _ in range(_DEFAULT_CLUSTER_LIMIT):
            if remaining <= _COIN:
                break
            remaining -= _COIN // 10
            sent = self.wallet.send_self_transfer(
                utxo_to_spend=prevout, sequence=0, fee=_COIN // 10
            )
            prevout = self.wallet.new_utxos(sent)[0]
            chain_txids.append(_txid(sent))

        # the child fees count, 4 BTC of them, so 3 BTC is refused
        dbl_tx = self.wallet.create_self_transfer(
            utxo_to_spend=tx0, sequence=0, fee=3 * _COIN
        )
        details = (
            f"insufficient fee, rejecting replacement {_txid(dbl_tx)}, "
            "less fees than conflicting txs; 3.00 < 4.00"
        )
        _assert_test_refused(self.node, dbl_tx, "insufficient fee", details)
        _assert_refused(self.node, dbl_tx, details)

        _set_value(dbl_tx, 0, _COIN // 10)
        _send(self.node, dbl_tx)
        mempool = _mempool(self.node)
        for txid in chain_txids:
            assert txid not in mempool

    def doublespend_tree(self) -> None:
        """Core's `test_doublespend_tree`."""
        initial = 5 * _COIN
        tx0 = self.make_utxo(initial)
        fee = 1000

        def branch(
            prevout: Utxo, initial_value: int, max_txs: int, total: list[int]
        ) -> Iterator[str]:
            if total[0] >= max_txs:
                return
            tree_width = 5
            txout_value = (initial_value - fee) // tree_width
            if txout_value < fee:
                return
            sent = self.wallet.send_self_transfer_multi(
                utxos_to_spend=[prevout],
                sequence=0,
                num_outputs=tree_width,
                fee_per_output=prevout.value // tree_width - txout_value,
            )
            yield _txid(sent)
            total[0] += 1
            for utxo in self.wallet.new_utxos(sent):
                yield from branch(utxo, txout_value, max_txs, total)

        n = _DEFAULT_CLUSTER_LIMIT
        tree_txs = list(branch(tx0, initial, n, [0]))
        assert len(tree_txs) == n

        # too little fee paid
        dbl_tx = self.wallet.create_self_transfer(
            utxo_to_spend=tx0, sequence=0, fee=fee * n
        )
        _assert_refused(self.node, dbl_tx, "insufficient fee")

        # 0.1 BTC more is enough
        dbl_tx = self.wallet.create_self_transfer(
            utxo_to_spend=tx0, sequence=0, fee=fee * n + _COIN // 10
        )
        _send(self.node, dbl_tx)
        mempool = _mempool(self.node)
        for txid in tree_txs:
            assert txid not in mempool

    def replacement_feeperkb(self) -> None:
        """Core's `test_replacement_feeperkb`."""
        tx0 = self.make_utxo(int(1.1 * _COIN))
        self.wallet.send_self_transfer(utxo_to_spend=tx0, sequence=0, fee=_COIN // 10)

        # a higher fee, but a fee rate much lower
        tx1b = _multi(self.wallet, [tx0], _SMALL_OUTPUT, num_outputs=100)
        _assert_refused(self.node, tx1b, self.rules.lower_feerate_refusal)

    def spends_of_conflicting_outputs(self) -> None:
        """Core's `test_spends_of_conflicting_outputs`."""
        utxo1 = self.make_utxo(int(1.2 * _COIN))
        utxo2 = self.make_utxo(3 * _COIN)
        tx1a = self.wallet.send_self_transfer(
            utxo_to_spend=utxo1, sequence=0, fee=_COIN // 10
        )
        tx1a_utxo = self.wallet.new_utxos(tx1a)[0]

        # a direct spend of an output of the transaction replaced
        tx2 = _multi(self.wallet, [utxo1, utxo2, tx1a_utxo], tx1a_utxo.value)
        reason = "bad-txns-spends-conflicting-tx"
        details = f"{reason}, {_txid(tx2)} spends conflicting transaction {_txid(tx1a)}"
        _assert_test_refused(self.node, tx2, reason, details)
        _assert_refused(self.node, tx2, details)

        # the indirect case
        tx1b = self.wallet.send_self_transfer(
            utxo_to_spend=tx1a_utxo, sequence=0, fee=_COIN // 10
        )
        tx1b_utxo = self.wallet.new_utxos(tx1b)[0]
        tx2 = _multi(self.wallet, [utxo1, utxo2, tx1b_utxo], tx1a_utxo.value)
        _assert_refused(self.node, tx2, reason)

    def new_unconfirmed_inputs(self) -> None:
        """Core's `test_new_unconfirmed_inputs`."""
        confirmed_utxo = self.make_utxo(int(1.1 * _COIN))
        unconfirmed_utxo = self.make_utxo(_UNCONFIRMED_COIN, confirmed=False)
        self.wallet.send_self_transfer(
            utxo_to_spend=confirmed_utxo, sequence=0, fee=_COIN // 10
        )
        tx2 = _multi(self.wallet, [confirmed_utxo, unconfirmed_utxo], _COIN)
        if self.rules.clustered:
            tx2_id = _send(self.node, tx2)
            assert tx2_id in _mempool(self.node)
        else:
            reason = "replacement-adds-unconfirmed"
            details = (
                f"{reason}, replacement {_txid(tx2)} adds unconfirmed input, idx 1"
            )
            _assert_test_refused(self.node, tx2, reason, details)
            _assert_refused(self.node, tx2, details)

    def new_unconfirmed_input_with_low_feerate(self) -> None:
        """Core's `test_new_unconfirmed_input_with_low_feerate`."""
        confirmed_utxos = [self.make_utxo(int(1.1 * _COIN)) for _ in range(3)]
        large_low_feerate = self.wallet.create_self_transfer(
            utxo_to_spend=confirmed_utxos[0], target_vsize=10_000, fee=1000
        )
        _send(self.node, large_low_feerate, None)
        unconfirmed_utxo = self.wallet.new_utxos(large_low_feerate)[0]

        # about the same size, the replacement paying twice the fee
        to_replace = self.wallet.create_self_transfer_multi(
            utxos_to_spend=[confirmed_utxos[1], confirmed_utxos[2]],
            fee_per_output=2000,
        )
        replacement = self.wallet.create_self_transfer_multi(
            utxos_to_spend=[confirmed_utxos[1], unconfirmed_utxo],
            fee_per_output=4000,
        )
        to_replace_fee = _fee_of(to_replace, confirmed_utxos[1:])
        replacement_fee = _fee_of(replacement, [confirmed_utxos[1], unconfirmed_utxo])
        assert replacement_fee * to_replace.vsize > to_replace_fee * replacement.vsize

        _send(self.node, to_replace, None)
        _assert_refused(
            self.node,
            replacement,
            self.rules.adds_unconfirmed_refusal,
            maxfeerate=None,
        )

    def too_many_replacements(self) -> None:
        """Core's `test_too_many_replacements`."""
        initial = 10 * _COIN
        utxo = self.make_utxo(initial)
        fee = 10_000
        split_value = (initial - fee) // (_MAX_REPLACEMENT_LIMIT + 1)
        splitting_tx = self.wallet.send_self_transfer_multi(
            utxos_to_spend=[utxo],
            sequence=0,
            num_outputs=_MAX_REPLACEMENT_LIMIT + 1,
            fee_per_output=initial // (_MAX_REPLACEMENT_LIMIT + 1) - split_value,
        )
        splitting_tx_utxos = self.wallet.new_utxos(splitting_tx)
        self.mine_mempool()

        # spend each of those outputs individually
        for spent in splitting_tx_utxos:
            self.wallet.send_self_transfer(utxo_to_spend=spent, sequence=0, fee=fee)

        # a doublespend of the whole lot, paying enough to cover them all
        # and at a higher fee rate
        double_spend_value = (split_value - 100 * fee) * (_MAX_REPLACEMENT_LIMIT + 1)
        double_tx = _multi(self.wallet, splitting_tx_utxos, double_spend_value)
        details = self.rules.too_many_details(_txid(double_tx))
        _assert_test_refused(
            self.node, double_tx, "too many potential replacements", details
        )
        _assert_refused(self.node, double_tx, details)

        # without an input, it is taken
        double_tx.vin.pop()
        _send(self.node, double_tx)

    def rpc(self, funded_wallet: str) -> None:
        """Core's `test_rpc`.

        :param funded_wallet: the node wallet `fundrawtransaction` draws on.
        """
        us0 = self.wallet.get_utxo()
        ins = [{"txid": us0.outpoint.tx_id.hex(), "vout": us0.outpoint.vout}]
        outs = {_UNSPENDABLE: 1}
        for replaceable, sequence in ((True, 4294967293), (False, 4294967295)):
            raw = self.node.rpc.call(
                "createrawtransaction", [ins, outs, 0, replaceable]
            )
            decoded = self.node.rpc.call("decoderawtransaction", [raw])
            assert decoded["vin"][0]["sequence"] == sequence, decoded

        wallet = self.node.rpc.for_wallet(funded_wallet)
        raw = self.node.rpc.call("createrawtransaction", [[], outs])
        for replaceable, sequence in ((True, 4294967293), (False, 4294967294)):
            funded = wallet.call(
                "fundrawtransaction", [raw, {"replaceable": replaceable}]
            )
            decoded = self.node.rpc.call("decoderawtransaction", [funded["hex"]])
            assert decoded["vin"][0]["sequence"] == sequence, decoded

    def prioritised_transactions(self) -> None:
        """Core's `test_prioritised_transactions`."""
        # the fee rate counts the modified fee
        tx0 = self.make_utxo(int(1.1 * _COIN))
        tx1a = self.wallet.send_self_transfer(
            utxo_to_spend=tx0, sequence=0, fee=_COIN // 10
        )
        tx1b = _multi(self.wallet, [tx0], _SMALL_OUTPUT, num_outputs=100)
        _assert_refused(self.node, tx1b, self.rules.lower_feerate_refusal)

        self.node.rpc.call(
            "prioritisetransaction", {"txid": _txid(tx1a), "fee_delta": -_COIN // 10}
        )
        tx1b_txid = _send(self.node, tx1b)
        assert tx1b_txid in _mempool(self.node)

        # the absolute fee counts it too
        tx1 = self.make_utxo(int(1.1 * _COIN))
        self.wallet.send_self_transfer(utxo_to_spend=tx1, sequence=0, fee=_COIN // 10)

        # a lower fee, which is prioritised
        tx2b = self.wallet.create_self_transfer(
            utxo_to_spend=tx1, sequence=0, fee=9 * _COIN // 100
        )
        _assert_refused(self.node, tx2b, "insufficient fee")
        self.node.rpc.call(
            "prioritisetransaction", {"txid": _txid(tx2b), "fee_delta": _COIN // 10}
        )
        tx2b_txid = _send(self.node, tx2b)
        assert tx2b_txid in _mempool(self.node)

    def replacement_relay_fee(self) -> None:
        """Core's `test_replacement_relay_fee`."""
        tx = self.wallet.send_self_transfer()

        # a higher fee and fee rate and a different txid, but the extra
        # fee is under the incremental relay fee, 100 satoshis per kvB
        info = self.node.rpc.call("getmempoolinfo")
        assert sats_from_btc(info["incrementalrelayfee"]) == 100, info
        _set_value(tx, 0, tx.vout[0].value - 1)
        _assert_refused(self.node, tx, "insufficient fee", maxfeerate=None)

    def fullrbf(self) -> None:
        """Core's `test_fullrbf`: BIP125 signalling is not respected."""
        confirmed_utxo = self.make_utxo(2 * _COIN)
        assert self.node.rpc.call("getmempoolinfo")["fullrbf"]

        # an explicitly opt-out transaction, which is ignored
        optout_tx = self.wallet.send_self_transfer(
            utxo_to_spend=confirmed_utxo,
            sequence=_MAX_BIP125_RBF_SEQUENCE + 1,
            fee_rate=_COIN // 100,
        )
        entry = self.node.rpc.call("getmempoolentry", [_txid(optout_tx)])
        assert entry["bip125-replaceable"] is False, entry

        conflicting_tx = self.wallet.create_self_transfer(
            utxo_to_spend=confirmed_utxo, fee_rate=2 * _COIN // 100
        )
        _send(self.node, conflicting_tx)
        mempool = _mempool(self.node)
        assert _txid(optout_tx) not in mempool
        assert _txid(conflicting_tx) in mempool

    def incremental_relay_feerates(self, datacarrier: str) -> None:
        """Core's `test_incremental_relay_feerates`.

        :param datacarrier: the `-datacarriersize` every restart carries
            besides `-incrementalrelayfee` and `-persistmempool=0`.
        """
        for setting in _INCREMENTAL_SETTINGS:
            decimal = Decimal(setting) / _COIN
            self.node.restart(
                [
                    f"-incrementalrelayfee={decimal:.8f}",
                    "-persistmempool=0",
                    datacarrier,
                ]
            )

            # the minimum relay fee rate is raised to the incremental one
            min_relay = self.node.rpc.call("getmempoolinfo")["minrelaytxfee"]
            assert min_relay >= decimal, (min_relay, decimal)

            low_feerate = sats_from_btc(min_relay) * 2
            coin = self.wallet.get_utxo(confirmed_only=True)
            # two versions, so that the failed replacement is not the
            # replaced transaction itself, and a size above the minimum, so
            # that one of a virtual byte less can follow
            replacee = self.wallet.create_self_transfer(
                utxo_to_spend=coin, fee_rate=low_feerate, version=3, target_vsize=200
            )
            _send(self.node, replacee, None)

            placeholder = self.wallet.create_self_transfer(
                utxo_to_spend=coin, target_vsize=200
            )
            # `get_fee`: the incremental fee of the replacement's size,
            # rounded up to a satoshi
            required_fee = -(-setting * placeholder.vsize // 1000) + _fee_of(
                replacee, [coin]
            )

            # one satoshi short of the required fee is refused
            failed = self.wallet.create_self_transfer(
                utxo_to_spend=coin, fee=required_fee - 1, version=2, target_vsize=200
            )
            _assert_refused(self.node, failed, "insufficient fee", maxfeerate=None)
            replacement = self.wallet.create_self_transfer(
                utxo_to_spend=coin, fee=required_fee, version=2, target_vsize=200
            )
            if setting == 0:
                # no additional fee is required, a higher fee rate still is
                _assert_refused(
                    self.node, replacement, "insufficient fee", maxfeerate=None
                )
                smaller = self.wallet.create_self_transfer(
                    utxo_to_spend=coin, fee=required_fee, version=2, target_vsize=199
                )
                _send(self.node, smaller, None)
            else:
                _send(self.node, replacement, None)


def replace_by_fee_follows_its_rules(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    for capability in (
        Capability.INCREMENTAL_RELAY_FEE,
        Capability.DATACARRIER,
        Capability.NODE_WALLET,
        Capability.GENERATE,
        Capability.MINE,
    ):
        require(capability, node.capabilities, skip_counts)
    rules = _Rules(clustered=Capability.CLUSTER_REPLACEMENT in node.capabilities)
    datacarrier = f"-datacarriersize={_DATACARRIER_SIZE}"
    limits = () if rules.clustered else _LEGACY_LIMITS
    node.restart(
        ["-deprecatedrpc=fullrbf", "-deprecatedrpc=bip125", datacarrier, *limits]
    )

    # Core's node wallet holds mature coins when its `test_rpc` runs
    node.rpc.call("createwallet", {"wallet_name": "rbf"})
    address = node.rpc.for_wallet("rbf").call("getnewaddress")
    node.rpc.call("generatetoaddress", [COINBASE_MATURITY + 1, address])

    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 20)
    rbf = _Rbf(node, wallet, rules)

    rbf.simple_doublespend()
    rbf.doublespend_chain()
    rbf.doublespend_tree()
    rbf.replacement_feeperkb()
    rbf.spends_of_conflicting_outputs()
    rbf.new_unconfirmed_inputs()
    rbf.new_unconfirmed_input_with_low_feerate()
    rbf.too_many_replacements()
    rbf.rpc("rbf")
    rbf.prioritised_transactions()
    rbf.replacement_relay_fee()
    rbf.fullrbf()
    rbf.incremental_relay_feerates(datacarrier)
