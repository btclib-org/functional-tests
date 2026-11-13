# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_balance`, one body over each wallet.

Read from Core's `test/functional/wallet_balance.py` (`fa6f7e36a8c2`,
2026-10-23): a wallet's balance buckets follow its coins through
unconfirmed sends, a fee bump, confirmation, an unload and a load, a
spend its node refuses, a replacement and a reorg; its last processed
block follows the chain; and a descriptor imported into a wallet in use
makes an output of a transaction it already holds its own. Every
assertion of Core's own is kept.

The body reaches its wallets only through `wallet.WalletAdapter`, made
by the `wallet.WalletFactory` its module hands it. Where Core asserts
the text of an error or the whole shape of a result, the body reads it
through `BitcoindWallet.rpc`, on bitcoind's wallet alone.

Each node of the pair is asked for the factory's node capabilities
first, then for `Capability.GENERATE`, `Capability.CONNECT` and
`Capability.INVALIDATE_BLOCK`; node 0 for `Capability.LIMIT_CLUSTER_COUNT`
and `Capability.CLOCK`; and the factory for `WalletCapability.RELOAD` and
`WalletCapability.IMPORT_DESCRIPTOR`. Core's harness mines to a key
it imports into each node's `default_wallet`; this mines to an address
of that wallet instead. Core's `noban_tx_relay` and `setup_network` are
spelled out: the noban permission at each start, and node 1 dialling
node 0.

Each `wallet_balance_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import secrets
import time
from decimal import Decimal
from typing import TYPE_CHECKING

from bip32_descriptors.descriptors.descriptors import add_checksum
from btclib.amount import sats_from_btc
from btclib.b58 import wif_from_prv_key
from btclib.key import PrvKeyData
from btclib.script.script_pub_key import ScriptPubKey, address
from btclib.tx import Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY
from ecc.curves import secp256k1

from btclib_functional_tests.bitcoind_wallet import BitcoindWallet
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import connect_nodes, sync_all, wait_until_tips_agree
from btclib_functional_tests.wallet import BlockRef, WalletCapability
from tests.integration.script_verify_flag_test import bitcoind_version
from tests.integration.wallet_signmessagewithaddress_test import refused

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import WalletAdapter, WalletFactory

__all__ = ["balances_follow_the_wallet_s_coins"]

# Core's harness's own `default_wallet_name` (`test_framework.py`)
_WALLET = "default_wallet"

# Core's `COIN` (`test_framework/messages.py`)
_COIN = 100_000_000

# Core's `MAX_BIP125_RBF_SEQUENCE` (`test_framework/messages.py`): the
# sequence `createrawtransaction` gives each input where `replaceable`
_MAX_BIP125_RBF_SEQUENCE = 0xFFFFFFFD

# Core's `ADDRESS_BCRT1_UNSPENDABLE` (`test_framework/address.py`): a
# witness v0 program of 32 zero bytes, which no key spends
_UNSPENDABLE = address(b"\x00\x20" + bytes(32), "regtest")

# what Core's own `noban_tx_relay` starts every node with
_NOBAN = "-whitelist=noban,in,out@127.0.0.1"

# `src/rpc/protocol.h`: `RPC_METHOD_DEPRECATED`, what `getbalance`
# answers a dummy argument other than "*"
_RPC_METHOD_DEPRECATED = -32

# the day Core's test moves node 0's clock forward by
_DAY = 86_400

# the keys of `getbalances`'s `mine`, Core's own result shape
_MINE_KEYS = {"trusted", "untrusted_pending", "immature", "nonmempool"}

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v32.0`, the first
# release whose `getbalances` answers `nonmempool` (bitcoin/bitcoin#33671)
_NONMEMPOOL_VERSION = 320000


def _sats(btc: str) -> int:
    """Return a bitcoin amount Core's test writes as a `Decimal`, in satoshi."""
    return sats_from_btc(Decimal(btc))


def _to(addr: str) -> ScriptPubKey:
    """Return the script `addr` pays to."""
    return ScriptPubKey.from_address(addr)


def _generate(
    node: NodeAdapter, count: int, addr: str, nodes: Sequence[NodeAdapter] = ()
) -> list[str]:
    """Core's `generatetoaddress`, then its `sync_all` over `nodes`, if any."""
    hashes: list[str] = node.rpc.call("generatetoaddress", [count, addr])
    if nodes:
        sync_all(nodes)
    return hashes


def _send(tx: Tx, *nodes: NodeAdapter) -> None:
    """Core's `sendrawtransaction` of `tx`, on each of `nodes` in turn."""
    for node in nodes:
        node.rpc.call("sendrawtransaction", [tx.serialize(include_witness=True).hex()])


def _create_transactions(
    wallet: WalletAdapter, addr: str, amount: int, fees: Sequence[int]
) -> list[Tx]:
    """Core's `create_transactions`: one signed payment of `amount` per fee.

    Each spends the same coins of `wallet`, enough for `amount` and the
    largest fee, and pays the rest to a change address of its own.
    """
    outpoints = []
    total = 0
    for utxo in wallet.utxos(0):
        outpoints.append(utxo.outpoint)
        total += utxo.tx_out.value
        if total >= amount + max(fees):
            break
    assert total >= amount + max(fees)

    txs = []
    for fee in fees:
        outputs = [TxOut(amount, _to(addr))]
        # no change output of zero
        if total > amount + fee:
            change = total - amount - fee
            outputs.append(TxOut(change, _to(wallet.new_change_address())))
        vin = [TxIn(o, sequence=_MAX_BIP125_RBF_SEQUENCE) for o in outpoints]
        txs.append(wallet.sign(Tx(2, 0, vin, outputs)))
    return txs


def _lowered(tx: Tx, value: int, lower: int) -> Tx:
    """Return `tx` unsigned, with its output of `value` paying `lower`."""
    vin = [TxIn(tx_in.prev_out, sequence=tx_in.sequence) for tx_in in tx.vin]
    vout = [
        TxOut(lower if o.value == value else o.value, o.script_pub_key) for o in tx.vout
    ]
    return Tx(tx.version, tx.lock_time, vin, vout)


def _new_key() -> tuple[str, ScriptPubKey]:
    """Core's `get_generate_key`: a fresh key's WIF, and its P2WPKH script."""
    prv_key = 1 + secrets.randbelow(secp256k1.n - 1)
    script = ScriptPubKey.p2wpkh(PrvKeyData(prv_key, network="regtest").pub)
    return wif_from_prv_key(prv_key, "regtest"), script


def _assert_balances(wallet: WalletAdapter, trusted: int, pending: int) -> None:
    """Assert `wallet`'s buckets: nothing immature, nothing outside the mempool.

    On bitcoind, also the keys of Core's own answer, `nonmempool` among them
    from `v32.0` on, where `Balances` carries `None` before it.
    """
    balances = wallet.balances()
    buckets = (balances.trusted, balances.untrusted_pending, balances.immature)
    assert buckets == (trusted, pending, 0), balances
    if not isinstance(wallet, BitcoindWallet):
        assert balances.nonmempool == 0, balances
        return
    version = bitcoind_version(wallet.node)
    assert version is not None
    keys = _MINE_KEYS if version >= _NONMEMPOOL_VERSION else _MINE_KEYS - {"nonmempool"}
    answer = wallet.rpc.call("getbalances")
    assert set(answer) == {"mine", "lastprocessedblock"}, answer
    assert set(answer["mine"]) == keys, answer
    assert balances.nonmempool == (0 if "nonmempool" in keys else None), balances


def _check_balances(w0: WalletAdapter, w1: WalletAdapter, fee_node_1: int) -> None:
    """Core's `test_balances`, after node 1's payment paid `fee_node_1`.

    Node 0 holds the change of its own payment, trusted, and node 1's
    payment, not; node 1 holds its own change, not trusted, since its
    payment spent node 0's unconfirmed one.
    """
    _assert_balances(w0, _sats("9.99"), 60 * _COIN)
    _assert_balances(w1, 0, 30 * _COIN - fee_node_1)
    # without arguments, unconfirmed included, untrusted not
    assert w0.balance() == _sats("9.99")
    assert w1.balance() == 0
    assert w0.balance(0) == _sats("9.99")
    assert w1.balance(0) == 0
    # with min_depth, a coin spent less than that many blocks ago is left
    # out: Core's own TODO on how getbalance tracks a spend's depth
    assert w0.balance(1) == 0
    assert w1.balance(1) == 0


def balances_follow_the_wallet_s_coins(
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
        for capability in sorted(wallets.node_capabilities, key=lambda c: c.value):
            require(capability, node.capabilities, skip_counts)
        for capability in (
            Capability.GENERATE,
            Capability.CONNECT,
            Capability.INVALIDATE_BLOCK,
        ):
            require(capability, node.capabilities, skip_counts)
    require(Capability.LIMIT_CLUSTER_COUNT, node0.capabilities, skip_counts)
    require(Capability.CLOCK, node0.capabilities, skip_counts)
    require(WalletCapability.RELOAD, wallets.capabilities, skip_counts)
    require(WalletCapability.IMPORT_DESCRIPTOR, wallets.capabilities, skip_counts)

    # a cluster limit low enough for the wallet's later payments to be refused
    node0.restart([_NOBAN, "-limitclustercount=3", *wallets.node_args])
    node1.restart([_NOBAN, *wallets.node_args])
    connect_nodes(node1, node0)
    w0 = wallets.create(node0, _WALLET)
    w1 = wallets.create(node1, _WALLET)

    _generate(node0, 1, w0.new_address(), nodes)
    _generate(node1, 1, w1.new_address(), nodes)

    # an immature coinbase is listed where asked for
    assert len(w0.utxos(include_immature_coinbase=True)) == 1
    assert len(w0.utxos(include_immature_coinbase=False)) == 0

    _generate(node1, COINBASE_MATURITY + 1, _UNSPENDABLE, nodes)

    assert len(w0.utxos(include_immature_coinbase=False)) == 1
    assert len(w0.utxos(include_immature_coinbase=True)) == 1

    # the balance at each depth
    assert w0.balance(1) == 50 * _COIN
    assert w0.balance(0) == 50 * _COIN
    assert w1.balance(0) == 50 * _COIN
    if isinstance(w0, BitcoindWallet):
        # Core's own spellings of the same call: the dummy argument, and
        # `include_watchonly`, which no longer does anything
        for params in (["*"], ["*", 1], ["*", 1, True]):
            assert w0.rpc.call("getbalance", params) == 50, params

    # node 0 pays node 1 40 coins, then node 1 pays node 0 60
    txs = _create_transactions(w0, w1.new_address(), 40 * _COIN, [_sats("0.01")])
    # sending on both nodes is faster than waiting for the relay
    _send(txs[0], node0, node1)
    sync_all(nodes)
    txs = _create_transactions(
        w1, w0.new_address(), 60 * _COIN, [_sats("0.01"), _sats("0.02")]
    )
    _send(txs[0], node1, node0)
    sync_all(nodes)

    if isinstance(w1, BitcoindWallet):
        refused(
            w1.rpc,
            "getbalance",
            [""],
            _RPC_METHOD_DEPRECATED,
            'dummy first argument must be excluded or set to "*"',
        )

    # balances with unconfirmed inputs
    _check_balances(w0, w1, _sats("0.01"))

    # node 1 bumps the fee of its payment
    _send(txs[1], node1, node0)
    sync_all(nodes)

    # the same balances, with node 1's first payment replaced by the bump
    _check_balances(w0, w1, _sats("0.02"))

    _generate(node1, 1, _UNSPENDABLE, nodes)

    # confirmed: node 1's payment and node 0's change, and node 1's change
    assert w0.balances().trusted == _sats("69.99")
    assert w1.balances().trusted == _sats("29.98")
    assert w0.balance() == _sats("69.99")
    assert w1.balance() == _sats("29.98")

    # node 1 sends its whole balance away
    txs = _create_transactions(w1, w0.new_address(), _sats("29.97"), [_sats("0.01")])
    _send(txs[0], node1)
    _generate(node1, 2, _UNSPENDABLE, nodes)

    # Core's own TODO again: neither depth shows the balance before the spend
    assert w1.balance(3) == 0
    assert w1.balance(2) == 0

    # a payment the wallet learns of while unloaded counts once it is loaded
    before = w1.balances().untrusted_pending
    dst = w1.new_address()
    w1.unload()
    w0.spend([TxOut(_sats("0.1"), _to(dst))])
    sync_all(nodes)
    w1.load()
    assert w1.balances().untrusted_pending == before + _sats("0.1")

    # three payments each spending every coin, the last a third
    # descendant of the payment above, which node 0's cluster limit refuses
    payments = [w0.spend([TxOut(99 * _COIN, _to(w0.new_address()))]) for _ in range(3)]
    tx_orig = payments[-1]
    txid = tx_orig.id

    # a payment outside the mempool is not trusted
    assert txid.hex() not in node0.rpc.call("getrawmempool")
    assert w0.transaction(txid).trusted is False
    assert w0.balance(0) == 0

    # a replacement of that payment, a coin more in fees, confirmed
    tx_replace = w0.sign(_lowered(tx_orig, 99 * _COIN, 98 * _COIN))
    # the balance is then every output of the replacement
    total_amount = sum(tx_out.value for tx_out in tx_replace.vout)
    sync_all(nodes)
    node1.rpc.call(
        "sendrawtransaction",
        {
            "hexstring": tx_replace.serialize(include_witness=True).hex(),
            "maxfeerate": 0,
        },
    )
    block_reorg = _generate(node1, 1, _UNSPENDABLE, nodes)[0]
    assert w0.balance(0) == total_amount

    # the replacement goes back to node 1's mempool, not to node 0's
    node0.rpc.call("invalidateblock", [block_reorg])
    node1.rpc.call("invalidateblock", [block_reorg])
    assert w0.balance(0) == 0
    _generate(node0, 1, _UNSPENDABLE)

    # the first payment is confirmed instead
    node1.restart([_NOBAN, "-persistmempool=0", *wallets.node_args])
    connect_nodes(node0, node1)
    wait_until_tips_agree(nodes)
    _send(tx_orig, node1)
    _generate(node1, 1, _UNSPENDABLE, nodes)
    # the reorg recovered the coin of fees
    assert w0.balance(0) == total_amount + _COIN

    # the last processed block follows the chain
    prev_hash = node0.rpc.call("getbestblockhash")
    prev_height = node0.rpc.call("getblock", [prev_hash])["height"]
    _generate(node0, 5, w0.new_address(), nodes)
    tip = w0.tip()
    assert tip.block_id.hex() != prev_hash
    assert tip.height == prev_height + 5
    if isinstance(w0, BitcoindWallet):
        last = w0.rpc.call("getbalances")["lastprocessedblock"]
        assert last == {"hash": tip.block_id.hex(), "height": tip.height}, last

    prev_hash = node0.rpc.call("getbestblockhash")
    prev_height = node0.rpc.call("getblock", [prev_hash])["height"]
    assert w0.tip() == BlockRef(prev_height, bytes.fromhex(prev_hash))

    tx = w1.spend([TxOut(_sats("0.01"), _to(w1.new_address()))])
    assert w1.tip() == BlockRef(prev_height, bytes.fromhex(prev_hash))
    if isinstance(w1, BitcoindWallet):
        info = w1.rpc.call("gettransaction", [tx.id.hex()])
        last = info["lastprocessedblock"]
        assert last == {"hash": prev_hash, "height": prev_height}, last

    # an import makes an output of a transaction the wallet holds its own
    importupdate = wallets.create(node0, "importupdate")
    wif1, script1 = _new_key()
    wif2, script2 = _new_key()
    importupdate.import_descriptor(add_checksum(f"wpkh({wif1})"))

    amount = 15 * _COIN
    w0.spend([TxOut(amount, script1), TxOut(amount, script2)])
    _generate(node0, 1, w0.new_address(), nodes)
    # a day on, so that "now" leaves out the block just mined, and eleven
    # blocks, so that the median time past moves beyond it
    node0.set_mock_time(int(time.time()) + _DAY)
    _generate(node0, 11, w0.new_address())

    assert importupdate.balances().trusted == amount

    # no rescan: the import updates the outputs the wallet already holds
    importupdate.import_descriptor(add_checksum(f"wpkh({wif2})"))
    assert importupdate.balances().trusted == amount * 2
