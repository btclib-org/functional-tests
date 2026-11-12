# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_signrawtransactionwithkey`, one body over each node.

Read from Core's `test/functional/rpc_signrawtransactionwithkey.py`
(`fa5f29774872`, 2025-12-16): the node signs a raw transaction's inputs
with private keys the caller hands it, over `signrawtransactionwithkey`
(`Capability.SIGN_RAW_TRANSACTION`), and reports the signing complete --
a P2PKH input described only by its `prevtxs` entry, a P2SH-P2WSH input
whose witnessScript is a 1-of-1 multisig, a P2PKH or a P2PK script, and
a pay-to-anchor input it "signs" with no key at all. Its refusals of a
sighash type it does not know, of a private key it cannot decode and of
a transaction that does not decode come last, in Core's own wording.
Each of Core's own subtests is a function here, run on a node of its
own, and every assertion Core's file makes is kept but the
`scantxoutset` one below.

`MiniWallet` (`Capability.MINE`) funds the outputs spent, as Core's own
`send_to` does; the subtests needing no coin ask for no capability but
the node's own signing.

What Core asks the node to build and not to sign, btclib builds here:
the unsigned transaction `createrawtransaction` would return -- version
2, `nLockTime` 0, and the `nSequence` its `replaceable` default gives --
and the 1-of-1 multisig's own redeemScript and output
`createmultisig` would, by `rpc_createmultisig_test.py`'s own
`multisig_script` and `multisig_script_pub_key`, whose answers
`rpc_createmultisig_bitcoind_test.py` holds `createmultisig`'s own to.
Core reads the funded P2SH-P2WSH output back over `scantxoutset` and
asserts its scriptPubKey is the one it computed; `MiniWallet.send_to`
returns the funding transaction, so its outpoint and its amount are read
off that instead, and the assertion is not kept, both sides of it being
btclib's here. The keys are random, as Core's `generate_keypair` makes
them, and Core's `get_deterministic_priv_key` is one more such key: the
subtests handing it over are about what the node refuses, not about the
key.

Each `rpc_signrawtransactionwithkey_<node>_test.py` module beside this
one runs it, `tests/integration/conftest.py`'s own module docstring
having how.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import pytest
from bitcoin_core_rpc import RpcError
from btclib.b58 import wif_from_prv_key
from btclib.key import PrvKeyData, PubKeyData
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY
from ecc.curves import secp256k1

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet
from tests.integration.rpc_createmultisig_test import (
    multisig_script,
    multisig_script_pub_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_pay_to_anchor_input_is_signed_with_no_key",
    "a_prevtxs_described_input_is_signed",
    "a_witness_script_input_is_signed",
    "an_invalid_private_key_or_transaction_is_refused",
    "an_invalid_sighash_type_is_refused",
]

# Core's own `INPUTS`, `OUTPUTS` and the keys `successful_signing_test`
# signs them with, copied from its file
_INPUTS: list[dict[str, Any]] = [
    {
        "txid": "9b907ef1e3c26fc71fe4a4b3580bc75264112f95050014157059c736f0202e71",
        "vout": 0,
        "scriptPubKey": "76a91460baa0f494b38ce3c940dea67f3804dc52d1fb9488ac",
    },
    {
        "txid": "83a4f6a6b73660e13ee6cb3c6063fa3759c50c9b7521d0536022961898f4fb02",
        "vout": 0,
        "scriptPubKey": "76a914669b857c03a5ed269d5d85a1ffac9ed5d663072788ac",
    },
]
_OUTPUT_ADDRESS = "mpLQjfK79b7CCV4VMJWEWAj5Mpx8Up5zxB"
_OUTPUT_VALUE = 10_000_000  # Core's own 0.1 BTC
_PRIV_KEYS = [
    "cUeKHd5orzT3mz8P9pxyREHfsWtVfgsfDjiZZBcjUBAaGk1BTj7N",
    "cVKpPfVKSJxKqVpE9awvXNWuLHCa5j5tiE7K6zbUSptFpTEtiFrA",
]

# the pay-to-anchor output, Core's own `p2a()` (`test_framework/address.py`):
# witness version 1 and the two-byte program `4e73`
_P2A = ScriptPubKey(bytes.fromhex("51024e73"), "regtest", check_validity=False)

# Core's own amounts, satoshi: what each funding sends, and what each
# spend pays on
_FUNDED = 4_999_900_000  # 49.999 BTC
_FUNDED_SPENT = 4_999_800_000  # 49.998 BTC
_WITNESS_SCRIPT_FUNDED = 1_000_000_000  # 10 BTC
_WITNESS_SCRIPT_SPENT = 999_900_000  # 9.999 BTC

# `createrawtransaction`'s own `nSequence` under its `replaceable` default
_MAX_BIP125_RBF_SEQUENCE = 0xFFFFFFFD

# `src/rpc/protocol.h`'s own codes for the refusals asserted below
_INVALID_PARAMETER = -8
_INVALID_ADDRESS_OR_KEY = -5
_DESERIALIZATION = -22


def _keypair() -> tuple[PubKeyData, str]:
    """Return a random key, as Core's `generate_keypair(wif=True)`.

    :returns: the compressed public key and the private key as regtest WIF.
    """
    q = 1 + secrets.randbelow(secp256k1.n - 1)
    return PrvKeyData(q, network="regtest").pub, wif_from_prv_key(q, "regtest")


def _new_destination() -> ScriptPubKey:
    """Return a fresh P2WPKH output, Core's `getnewdestination()[2]`."""
    return ScriptPubKey.p2wpkh(_keypair()[0])


def _unsigned(outpoints: Sequence[OutPoint], outputs: Sequence[TxOut]) -> str:
    """Return what `createrawtransaction` returns for these inputs, outputs."""
    tx = Tx(
        version=2,
        lock_time=0,
        vin=[TxIn(outpoint, b"", _MAX_BIP125_RBF_SEQUENCE) for outpoint in outpoints],
        vout=list(outputs),
    )
    return tx.serialize(include_witness=True).hex()


def _btc(value: int) -> str:
    """Return a satoshi `value` as Core's own eight-decimal BTC string."""
    return f"{value // 100_000_000}.{value % 100_000_000:08d}"


def _assert_signing_completed_successfully(signed: dict[str, object]) -> None:
    """Core's own `assert_signing_completed_successfully`."""
    assert "errors" not in signed
    assert signed["complete"] is True


def _refused(
    node: NodeAdapter,
    params: Sequence[object] | dict[str, object],
    code: int,
    message: str,
) -> None:
    """Assert `signrawtransactionwithkey` refuses `params`, as Core asserts it.

    :param code: the node's own error code, `src/rpc/protocol.h`.
    :param message: a substring of the node's own error message.
    """
    with pytest.raises(RpcError) as refusal:
        node.rpc.call("signrawtransactionwithkey", params)
    assert refusal.value.code == code, str(refusal.value)
    assert message in refusal.value.args[0], str(refusal.value)


def _started(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> NodeAdapter:
    """Start one node, and check it signs."""
    (node,) = cluster(1)
    require(Capability.SIGN_RAW_TRANSACTION, node.capabilities, skip_counts)
    return node


def _funded(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> tuple[NodeAdapter, MiniWallet]:
    """Start one node, check it signs and mines, and mature coins."""
    node = _started(cluster, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 2)
    return node, wallet


def _core_unsigned() -> str:
    """Return the transaction Core's own `INPUTS` and `OUTPUTS` describe."""
    return _unsigned(
        [OutPoint(bytes.fromhex(i["txid"]), i["vout"]) for i in _INPUTS],
        [TxOut(_OUTPUT_VALUE, ScriptPubKey.from_address(_OUTPUT_ADDRESS))],
    )


def a_prevtxs_described_input_is_signed(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's `successful_signing_test`: inputs the node knows by `prevtxs`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _started(cluster, skip_counts)
    signed = node.rpc.call(
        "signrawtransactionwithkey", [_core_unsigned(), _PRIV_KEYS, _INPUTS]
    )
    _assert_signing_completed_successfully(signed)


def a_witness_script_input_is_signed(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's `witness_script_test`: 1-of-1 multisig, then P2PKH and P2PK.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _funded(cluster, skip_counts)

    pub, priv = _keypair()
    redeem_script = multisig_script(1, [pub.sec.hex()])
    spk = multisig_script_pub_key(redeem_script, "p2sh-segwit")
    funding = wallet.send_to(spk, _FUNDED)
    wallet.generate(1, confirm=[funding])
    prevtx = {
        "txid": funding.id.hex(),
        "vout": 1,
        "scriptPubKey": spk.script.hex(),
        "amount": _btc(_FUNDED),
        "witnessScript": redeem_script.hex(),
        "redeemScript": ScriptPubKey.p2wsh(redeem_script).script.hex(),
    }
    spending_tx = _unsigned(
        [OutPoint(funding.id, 1)], [TxOut(_FUNDED_SPENT, _new_destination())]
    )
    signed = node.rpc.call("signrawtransactionwithkey", [spending_tx, [priv], [prevtx]])
    _assert_signing_completed_successfully(signed)

    # these are order-independent in Core's own file
    for script_type in ("P2PKH", "P2PK"):
        pub, priv = _keypair()
        witness_script = (
            ScriptPubKey.p2pkh(pub)
            if script_type == "P2PKH"
            else ScriptPubKey.p2pk(pub)
        ).script
        redeem_script = ScriptPubKey.p2wsh(witness_script).script
        spk = ScriptPubKey.p2sh(redeem_script, "regtest")
        funding = wallet.send_to(spk, _WITNESS_SCRIPT_FUNDED)
        prevtx = {
            "txid": funding.id.hex(),
            "vout": 1,
            "scriptPubKey": spk.script.hex(),
            "redeemScript": redeem_script.hex(),
            "witnessScript": witness_script.hex(),
            "amount": _btc(_WITNESS_SCRIPT_FUNDED),
        }
        spending_tx = _unsigned(
            [OutPoint(funding.id, 1)],
            [TxOut(_WITNESS_SCRIPT_SPENT, _new_destination())],
        )
        signed = node.rpc.call(
            "signrawtransactionwithkey", [spending_tx, [priv], [prevtx]]
        )
        _assert_signing_completed_successfully(signed)
        node.rpc.call("sendrawtransaction", [signed["hex"]])


def a_pay_to_anchor_input_is_signed_with_no_key(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's `keyless_signing_test`: nothing to sign, and nothing changed.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _funded(cluster, skip_counts)
    funding = wallet.send_to(_P2A, _FUNDED)
    spending_tx = _unsigned(
        [OutPoint(funding.id, 1)], [TxOut(_FUNDED_SPENT, _new_destination())]
    )
    signed = node.rpc.call("signrawtransactionwithkey", [spending_tx, [], []])
    _assert_signing_completed_successfully(signed)
    assert node.rpc.call("testmempoolaccept", [[signed["hex"]]])[0]["allowed"]
    # "signing" a P2A prevout is a no-op, so the two do not differ
    assert signed["hex"] == spending_tx


def an_invalid_sighash_type_is_refused(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's `invalid_sighashtype_test`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _started(cluster, skip_counts)
    _refused(
        node,
        {
            "hexstring": _core_unsigned(),
            "privkeys": [_keypair()[1]],
            "sighashtype": "all",
        },
        _INVALID_PARAMETER,
        "'all' is not a valid sighash parameter.",
    )


def an_invalid_private_key_or_transaction_is_refused(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's `invalid_private_key_and_tx`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _started(cluster, skip_counts)
    tx = _core_unsigned()
    _refused(node, [tx, ["123"]], _INVALID_ADDRESS_OR_KEY, "Invalid private key")
    _refused(
        node,
        [f"{tx}00", [_keypair()[1]]],
        _DESERIALIZATION,
        "TX decode failed. Make sure the tx has at least one input.",
    )
