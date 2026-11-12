# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_nulldummy`, one body over each node.

Read from Core's `test/functional/feature_nulldummy.py` (`fa5f29774872`,
2025-12-16), every step of it kept in its own order
([ISS 64](https://github.com/btclib-org/functional-tests/issues/64)):
`-testactivationheight=segwit@N` (`Capability.TEST_ACTIVATION_HEIGHT`)
holds segwit, and NULLDUMMY (BIP147) with it, inactive until a chosen
height, and each step spends a multisig output whose dummy element --
the extra stack item `OP_CHECKMULTISIG` pops beyond its signatures -- is
empty or is `OP_TRUE`, in a P2SH scriptSig or a P2SH-P2WSH witness.
Before activation the mempool refuses a non-empty dummy and a block
still carries it; from the configured height on both refuse it, and an
empty dummy is still accepted by both.

The multisig is 1-of-1, as Core's own is, and every spend carries its
signature: `EvalScript` (`src/script/interpreter.cpp`) reaches the dummy
only past its signature loop, so a node is asked to find the dummy
beneath a signature, as Core's own file asks it. Each signature is
btclib's own -- `btclib.script.sig_hash`'s `legacy` for the P2SH spends
and `segwit_v0` for the P2SH-P2WSH one, `ecc.dsa.sign_` over
either -- and neither sighash commits to a scriptSig or a witness, so
the tampered dummy leaves it valid and NULLDUMMY the only refusal.
Nothing here asserts a claim about the signature itself. Under the
mempool's own flags a failing signature is refused on
`SCRIPT_ERR_SIG_NULLFAIL` before the dummy is read, so a bad btclib
signature fails the first `sendrawtransaction` carrying one, on an
error other than NULLDUMMY's -- a finding that reproduces with btclib
alone, and so is btclib's (`CONTRIBUTING.md`'s *This repository in
particular*). A block is verified without NULLFAIL and still reads the
dummy: a bad signature beneath an `OP_TRUE` dummy is refused there on
NULLDUMMY all the same.

The two coins spent first are coinbases paying the P2SH output directly,
where Core's own pay a P2PKH key `signrawtransactionwithkey` signs for,
and each block carrying a step's transactions is built client-side
(`btclib.block.build`, `btclib.block.mining.mine`) where Core's own
asks `getblocktemplate`. `MiniWallet`'s own coins are P2TR, spent
through a witness, and bitcoind refuses a block carrying a witness
before segwit activates (`unexpected-witness`), so no `MiniWallet` coin
funds a step mined before the configured height; `MiniWallet.generate`
only matures the two coinbases. Every spend pays the P2SH output, the
witness one's funding aside, where Core's own steps 4 and 5 pay
`getnewdestination`'s fresh addresses. Core's own `-addresstype=legacy`
is dropped: it sets the address type of a node wallet, and this body
uses none. The node starts without the option and is restarted with
it before any block is mined.

Each `feature_nulldummy_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError
from btclib.key import PrvKeyData
from btclib.script import sig_hash
from btclib.script.script import serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.script.witness import Witness
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY
from ecc.dsa import sign_

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet, build_next_block
from tests.integration.script_verify_flag_test import block_script_verify_flag_failed

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.block.block import Block

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["nulldummy_is_policy_before_activation_and_consensus_after"]

# Core's own height: two coinbases, COINBASE_MATURITY blocks maturing
# them, two blocks before activation and the first one after
_SEGWIT_HEIGHT = COINBASE_MATURITY + 5

_NULLDUMMY_TX_ERROR = (
    "mempool-script-verify-flag-failed (Dummy CHECKMULTISIG argument must be zero)"
)
_NULLDUMMY_BLK_REASON = " (Dummy CHECKMULTISIG argument must be zero)"

# RPC_VERIFY_REJECTED, `src/rpc/protocol.h`
_RPC_VERIFY_REJECTED = -26

# a fixed key rather than Core's own random one, so that a run is
# reproducible; its public key is secp256k1's own generator
_PRV_KEY = PrvKeyData(1, "regtest")

_MULTISIG = ScriptPubKey.p2ms(1, [_PRV_KEY.pub]).script
_P2SH = ScriptPubKey.p2sh(_MULTISIG, "regtest")
_P2WSH = ScriptPubKey.p2wsh(_MULTISIG, "regtest")
_P2SH_P2WSH = ScriptPubKey.p2sh(_P2WSH.script, "regtest")

# Core's own amounts, whole bitcoins of a 50-bitcoin coinbase
_COIN = 100_000_000


def _submit_block(
    node: NodeAdapter, transactions: list[Tx], script_pub_key: ScriptPubKey
) -> tuple[str | None, Block]:
    """Build, solve and submit a block extending `node`'s own tip.

    Core's own `block_submit`: `build_block` adds the witness commitment
    where a transaction carries a witness, as Core's own
    `add_witness_commitment` does where it is asked to.

    :returns: `submitblock`'s own answer, and the block.
    """
    block = build_next_block(node, script_pub_key, transactions)
    answer = node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    return answer, block


def _assert_block(node: NodeAdapter, transactions: list[Tx], *, accept: bool) -> None:
    """Submit a block carrying `transactions`; assert it is `accept`-ed or not.

    Refused, the answer is NULLDUMMY's own and the tip has not moved.
    """
    old_tip = node.rpc.call("getbestblockhash")
    answer, block = _submit_block(node, transactions, _P2SH)
    if accept:
        assert answer is None
        assert node.rpc.call("getbestblockhash") == block.header.hash.hex()
    else:
        assert answer == (
            f"{block_script_verify_flag_failed(node)}{_NULLDUMMY_BLK_REASON}"
        )
        assert node.rpc.call("getbestblockhash") == old_tip


def _send(node: NodeAdapter, tx: Tx) -> None:
    """Broadcast `tx`, `maxfeerate` 0 as Core's own file passes it."""
    txid = node.rpc.call(
        "sendrawtransaction", [tx.serialize(True, check_validity=False).hex(), 0]
    )
    assert txid == tx.id.hex()


def _assert_refused(node: NodeAdapter, tx: Tx) -> None:
    """Assert the mempool refuses `tx` for its dummy element alone."""
    with pytest.raises(RpcError, match=re.escape(_NULLDUMMY_TX_ERROR)) as refusal:
        _send(node, tx)
    assert refusal.value.code == _RPC_VERIFY_REJECTED


def _signature(digest: bytes) -> bytes:
    """Return `_PRV_KEY`'s DER signature over `digest`, and `SIGHASH_ALL`."""
    signature = sign_(digest, _PRV_KEY.q)
    assert not isinstance(signature, tuple)
    return signature.serialize() + bytes([sig_hash.ALL])


def _base_spend(
    prev_out: OutPoint,
    value: int,
    *,
    dummy: str = "OP_0",
    script_pub_key: ScriptPubKey = _P2SH,
) -> Tx:
    """Return a signed `_P2SH` spend, `dummy` its scriptSig's first push.

    `OP_TRUE` is Core's own `invalidate_nulldummy_tx`.
    """
    tx_out = TxOut(value, script_pub_key)
    unsigned = TxIn(prev_out, sequence=0xFFFFFFFF)
    digest = sig_hash.legacy(
        _MULTISIG,
        Tx(version=2, lock_time=0, vin=[unsigned], vout=[tx_out]),
        0,
        sig_hash.ALL,
    )
    script_sig = serialize([dummy, _signature(digest), _MULTISIG])
    tx_in = TxIn(prev_out, script_sig=script_sig, sequence=0xFFFFFFFF)
    return Tx(version=2, lock_time=0, vin=[tx_in], vout=[tx_out])


def _witness_spend(
    prev_out: OutPoint, amount: int, value: int, *, dummy: bytes = b""
) -> Tx:
    r"""Return a signed `_P2SH_P2WSH` spend, `dummy` its first witness item.

    `b"\x01"` is Core's own tampered `scriptWitness.stack[0]`.

    :param amount: the coin's own value, which BIP143's sighash commits to.
    """
    tx_out = TxOut(value, _P2SH)
    script_sig = serialize([_P2WSH.script])
    unsigned = TxIn(prev_out, script_sig=script_sig, sequence=0xFFFFFFFF)
    digest = sig_hash.segwit_v0(
        _MULTISIG,
        Tx(version=2, lock_time=0, vin=[unsigned], vout=[tx_out]),
        0,
        sig_hash.ALL,
        amount,
    )
    tx_in = TxIn(
        prev_out,
        script_sig=script_sig,
        sequence=0xFFFFFFFF,
        script_witness=Witness([dummy, _signature(digest), _MULTISIG]),
    )
    return Tx(version=2, lock_time=0, vin=[tx_in], vout=[tx_out])


def nulldummy_is_policy_before_activation_and_consensus_after(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check Core's own steps, in its own order, on one chain.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.TEST_ACTIVATION_HEIGHT, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart([f"-testactivationheight=segwit@{_SEGWIT_HEIGHT}"])
    coinbases = []
    for _ in range(2):
        answer, block = _submit_block(node, [], _P2SH)
        assert answer is None
        coinbases.append(block.transactions[0])
    MiniWallet(node).generate(COINBASE_MATURITY)

    # 1: compliant base spends, and a spend funding the witness
    # multisig, are relayed and mined before activation
    test1txs = [_base_spend(OutPoint(coinbases[0].id, 0), 49 * _COIN)]
    _send(node, test1txs[0])
    test1txs.append(_base_spend(OutPoint(test1txs[0].id, 0), 48 * _COIN))
    _send(node, test1txs[1])
    funding = _base_spend(
        OutPoint(coinbases[1].id, 0), 49 * _COIN, script_pub_key=_P2SH_P2WSH
    )
    test1txs.append(funding)
    _send(node, test1txs[2])
    _assert_block(node, test1txs, accept=True)

    # 2: a non-compliant base spend is refused by the mempool
    test2tx = _base_spend(OutPoint(test1txs[1].id, 0), 47 * _COIN, dummy="OP_TRUE")
    _assert_refused(node, test2tx)

    # 3: and still mined, the block before activation
    _assert_block(node, [test2tx], accept=True)

    # 4: from the configured height on, it is refused by both
    test4tx = _base_spend(OutPoint(test2tx.id, 0), 46 * _COIN, dummy="OP_TRUE")
    _assert_refused(node, test4tx)
    _assert_block(node, [test4tx], accept=False)

    # 5: and so is a non-compliant P2SH-P2WSH spend
    test5tx = _witness_spend(
        OutPoint(funding.id, 0), 49 * _COIN, 48 * _COIN, dummy=b"\x01"
    )
    _assert_refused(node, test5tx)
    _assert_block(node, [test5tx], accept=False)

    # 6: the same two spends with an empty dummy are relayed and mined
    test6txs = [
        _base_spend(OutPoint(test2tx.id, 0), 46 * _COIN),
        _witness_spend(OutPoint(funding.id, 0), 49 * _COIN, 48 * _COIN),
    ]
    for tx in test6txs:
        _send(node, tx)
    _assert_block(node, test6txs, accept=True)
