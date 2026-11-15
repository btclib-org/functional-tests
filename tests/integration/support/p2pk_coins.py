# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""The no-witness coins and transactions of Core's `RAW_P2PK` wallet.

Core builds a transaction whose txid is its wtxid with a
`MiniWalletMode.RAW_P2PK` wallet. `mine_p2pk_coins` mines coinbases paying
`RAW_P2PK_SCRIPT_PUB_KEY` on a node, and `p2pk_tx`, `p2pk_self_transfer`
and `p2pk_self_transfer_multi` spend them under `raw_p2pk_script_sig`
(`mini_wallet.py`), which `MiniWallet` does not.

This module holds no test: its name ends `_test` for the repository's
`name-tests-test` hook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from btclib import var_int
from btclib.script.script import serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxIn, TxOut

from btclib_functional_tests.mini_wallet import (
    DEFAULT_FEE_RATE,
    FEE,
    RAW_P2PK_SCRIPT_PUB_KEY,
    Utxo,
    build_next_block,
    raw_p2pk_script_sig,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "mine_p2pk_coins",
    "p2pk_new_utxo",
    "p2pk_self_transfer",
    "p2pk_self_transfer_multi",
    "p2pk_tx",
]

# Core's own virtual size of a `RAW_P2PK` self-transfer, which its
# `create_self_transfer` (`wallet.py`) prices the fee at
_P2PK_VSIZE = 168

# how many signings `p2pk_tx` tries before it raises rather than keeps
# looking for one whose size matches `target_vsize`
_SIGNING_ATTEMPTS = 64


def mine_p2pk_coins(node: NodeAdapter, count: int) -> list[Utxo]:
    """Return `count` coins spent with no witness, mined on the node's tip.

    Core's own `generate(self.wallet_nonsegwit, count)`: `count` blocks,
    each built by `build_next_block` and submitted, whose coinbase pays
    `RAW_P2PK_SCRIPT_PUB_KEY`. Each coin is that coinbase's own output, a
    `Utxo` that `raw_p2pk_script_sig` spends rather than `MiniWallet`,
    spendable in the next block once `COINBASE_MATURITY - 1` blocks are
    mined on it.

    :raises TypeError: `submitblock` answered anything but acceptance.
    """
    coins = []
    for _ in range(count):
        block = build_next_block(node, RAW_P2PK_SCRIPT_PUB_KEY)
        answer = node.rpc.call(
            "submitblock", [block.serialize(check_validity=False).hex()]
        )
        if answer is not None:
            err_msg = f"submitblock refused a coinbase paying P2PK: {answer!r}"
            raise TypeError(err_msg)
        coinbase = block.transactions[0]
        height = node.rpc.call("getblockcount")
        coins.append(
            Utxo(OutPoint(coinbase.id, 0), coinbase.vout[0].value, height, True)
        )
    return coins


def p2pk_tx(coins: Sequence[Utxo], fee: int, *, target_vsize: int = 0) -> Tx:
    """Return a signed tx spending `coins` into one `RAW_P2PK` output.

    Core's own `create_self_transfer_multi` in `RAW_P2PK` mode
    (`wallet.py`), paying `fee` and padded, where `target_vsize` is
    nonzero, by an `OP_RETURN` output of `OP_1` opcodes to exactly that
    many virtual bytes, as Core's own `bulk_vout`
    (`test_framework/script_util.py`) pads it. Every input is signed after
    the padding, which is sized for the signatures of the attempt before.
    A signature's length moves with what it signs, and btclib's nonce is
    deterministic, so each padding after the first also takes one more
    satoshi off the output, for a different transaction to sign, where
    Core's own `sign_tx` draws a random nonce until the length is fixed.

    :raises RuntimeError: no attempt within `_SIGNING_ATTEMPTS` reached
        `target_vsize`.
    """
    value = sum(coin.value for coin in coins) - fee
    tx = Tx(
        version=2,
        lock_time=0,
        vin=[TxIn(coin.outpoint) for coin in coins],
        vout=[TxOut(value, RAW_P2PK_SCRIPT_PUB_KEY)],
        check_validity=False,
    )
    for attempt in range(_SIGNING_ATTEMPTS):
        for vin_i, tx_in in enumerate(tx.vin):
            tx_in.script_sig = raw_p2pk_script_sig(tx, vin_i)
        if not target_vsize or tx.vsize == target_vsize:
            return tx
        # the padding output: its value, its script's length prefix, and
        # the `OP_RETURN` opcode leave the rest to `OP_1` opcodes
        del tx.vout[1:]
        deficit = target_vsize - tx.vsize - 8
        length = next(
            deficit - size
            for size in (1, 3, 5)
            if len(var_int.serialize(deficit - size)) == size
        )
        padding = serialize(["OP_RETURN", *(["OP_1"] * (length - 1))])
        tx.vout[0] = TxOut(value - attempt, RAW_P2PK_SCRIPT_PUB_KEY)
        tx.vout.append(TxOut(0, ScriptPubKey(padding, check_validity=False)))
    err_msg = f"no signature reached target_vsize {target_vsize}"
    raise RuntimeError(err_msg)


def p2pk_self_transfer(
    coin: Utxo, fee_rate: int = DEFAULT_FEE_RATE, *, target_vsize: int = 0
) -> Tx:
    """Core's own `create_self_transfer` of its `RAW_P2PK` wallet.

    Its fee: `fee_rate`, in satoshis per 1000 virtual bytes, over Core's
    own `RAW_P2PK` virtual size, or over `target_vsize` where nonzero,
    rounded up, plus a satoshi for each signing `p2pk_tx` retries to
    reach that size.
    """
    fee = -(-fee_rate * (target_vsize or _P2PK_VSIZE) // 1000)
    return p2pk_tx([coin], fee, target_vsize=target_vsize)


def p2pk_self_transfer_multi(coins: Sequence[Utxo]) -> Tx:
    """Core's own `create_self_transfer_multi` of its `RAW_P2PK` wallet."""
    return p2pk_tx(coins, FEE)


def p2pk_new_utxo(tx: Tx) -> Utxo:
    """Core's own `new_utxo` of a `RAW_P2PK` transfer: its first output."""
    return Utxo(OutPoint(tx.id, 0), tx.vout[0].value, 0, False)
