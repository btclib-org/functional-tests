# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_createmultisig` spend half, one body over each node.

Read from Core's `test/functional/rpc_createmultisig.py` (`771200ca4362`,
2026-06-30): `do_multisig` funds a multisig output, has the node sign a
spend of it twice over disjoint key sets, and has the node combine the
two into one transaction a block confirms. The node signs and combines,
over `signrawtransactionwithkey` and `combinerawtransaction`
(`Capability.SIGN_RAW_TRANSACTION`), with keys the caller hands it; its
refusals of a `prevtxs` entry whose scripts are missing or do not match
come first, in Core's own wording. `MiniWallet` (`Capability.MINE`) funds
the output and mines both blocks.

What Core asks the node to build and not to sign, btclib builds here:
the multisig's redeemScript and output, by `multisig_script` and
`multisig_script_pub_key` below, whose answers
`rpc_createmultisig_bitcoind_test.py` holds `createmultisig`'s own to,
and the unsigned spend `createrawtransaction` would return -- version
2, `nLockTime` 0, and the `nSequence` its `replaceable` default gives.
The keys are random, as Core's `generate_keypair` makes them, and the
spend pays a P2WPKH output over another, Core's
`getnewdestination('bech32')`.

`test_combinerawtransaction_preconditions` is Core's `do_multisig` with
`assert_mergeability`, so it is here too: `combinerawtransaction`'s own
refusals of an empty list, of a single transaction, of one that does not
decode, and of one differing from the first in an amount, a version, a
lock time, an output, a sequence or an input's own index, and its answer
to a transaction combined with itself. Not every build refuses all of
them, so the caller says which the running node is expected to.
`test_multisig_script_limit`'s own 16-of-20 spends, `p2sh-segwit` and
`bech32`, are here too.

Each `rpc_createmultisig_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError
from btclib.b58 import wif_from_prv_key
from btclib.key import PrvKeyData
from btclib.script.script import serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY
from ecc.curves import secp256k1

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "M_OF_N",
    "OUTPUT_TYPES",
    "combinerawtransaction_preconditions",
    "every_multisig_spends_once_the_node_combines_its_signatures",
    "multisig_script",
    "multisig_script_pub_key",
    "sixteen_of_twenty_spends_once_the_node_combines_its_signatures",
    "wrapped",
]

# Core's own `m_of_n` list, and the output types `do_multisig` takes
M_OF_N = [(2, 3), (3, 3), (2, 5), (3, 5), (10, 15), (15, 15)]
OUTPUT_TYPES = ("legacy", "p2sh-segwit", "bech32")

# `do_multisig`'s own amounts, satoshi: the funded output, and what the
# spend pays on once Core's own fee is deducted
_VALUE = 4_000
_OUT_VALUE = 2_000
_VALUE_BTC = "0.00004000"

# `createrawtransaction`'s own `nSequence` under its `replaceable` default
_MAX_BIP125_RBF_SEQUENCE = 0xFFFFFFFD

# the largest count a small-integer opcode pushes
_OP_16 = 16

# `src/rpc/protocol.h`'s own codes for the refusals asserted below
_INVALID_PARAMETER = -8
_DESERIALIZATION = -22
_VERIFY_ERROR = -25


def wrapped(nsigs: int, keys: Sequence[str], output_type: str) -> str:
    """Return the unchecksummed descriptor `createmultisig` would report.

    :param nsigs: the signatures required.
    :param keys: the public keys, SEC hex, in order.
    :param output_type: `legacy`, `p2sh-segwit` or `bech32`.
    """
    multi = f"multi({nsigs},{','.join(keys)})"
    if output_type == "legacy":
        return f"sh({multi})"
    if output_type == "p2sh-segwit":
        return f"sh(wsh({multi}))"
    return f"wsh({multi})"


def multisig_script(nsigs: int, keys: Sequence[str]) -> bytes:
    """Return the `OP_CHECKMULTISIG` script, Core's `keys_to_multisig_script`.

    Built from the script's own items rather than by `ScriptPubKey.p2ms`,
    which refuses more keys than `OP_16` can count: a count past it is a
    minimal push, as Core's own `CScript` writes one, and a count up to it
    the small-integer opcode.

    :param nsigs: the signatures required.
    :param keys: the public keys, SEC hex, in order.
    """
    return serialize(
        [
            _small_integer(nsigs),
            *(bytes.fromhex(key) for key in keys),
            _small_integer(len(keys)),
            "OP_CHECKMULTISIG",
        ]
    )


def _small_integer(count: int) -> int | str:
    """Return `count` as `CScript` writes it: `OP_<n>` up to 16, else a push."""
    return f"OP_{count}" if count <= _OP_16 else count


def multisig_script_pub_key(script: bytes, output_type: str) -> ScriptPubKey:
    """Return the output `createmultisig` would give `script` for `output_type`.

    :param script: the redeemScript, `multisig_script`'s own answer.
    :param output_type: `legacy`, `p2sh-segwit` or `bech32`.
    """
    if output_type == "legacy":
        return ScriptPubKey.p2sh(script, "regtest")
    if output_type == "p2sh-segwit":
        return ScriptPubKey.p2sh(
            ScriptPubKey.p2wsh(script, "regtest").script, "regtest"
        )
    return ScriptPubKey.p2wsh(script, "regtest")


def _keypairs(count: int) -> tuple[list[str], list[str]]:
    """Return `count` random keys, as Core's `generate_keypair(wif=True)`.

    :returns: the public keys, compressed SEC hex, and the private keys as
        regtest WIF, in the same order.
    """
    scalars = [1 + secrets.randbelow(secp256k1.n - 1) for _ in range(count)]
    pub = [PrvKeyData(q, network="regtest").pub.sec.hex() for q in scalars]
    priv = [wif_from_prv_key(q, "regtest") for q in scalars]
    return pub, priv


def _new_destination() -> ScriptPubKey:
    """Return a fresh P2WPKH output, Core's `getnewdestination('bech32')`."""
    q = 1 + secrets.randbelow(secp256k1.n - 1)
    return ScriptPubKey.p2wpkh(PrvKeyData(q, network="regtest").pub)


def _unsigned(
    outpoint: OutPoint,
    destination: ScriptPubKey,
    value: int,
    *,
    version: int = 2,
    lock_time: int = 0,
    sequence: int = _MAX_BIP125_RBF_SEQUENCE,
) -> str:
    """Return what `createrawtransaction` returns for one input and output."""
    tx = Tx(
        version=version,
        lock_time=lock_time,
        vin=[TxIn(outpoint, b"", sequence)],
        vout=[TxOut(value, destination)],
    )
    return tx.serialize(include_witness=True).hex()


def _refused(
    node: NodeAdapter, method: str, params: list[object], code: int, message: str
) -> None:
    """Assert `method` is refused, as Core's `assert_raises_rpc_error` asserts.

    :param code: the node's own error code, `src/rpc/protocol.h`.
    :param message: a substring of the node's own error message.
    """
    with pytest.raises(RpcError) as refusal:
        node.rpc.call(method, params)
    assert refusal.value.code == code, (method, str(refusal.value))
    assert message in refusal.value.args[0], (method, str(refusal.value))


def _combinerawtransaction_refusals(
    node: NodeAdapter,
    outpoint: OutPoint,
    out_spk: ScriptPubKey,
    rawtx2: str,
    rawtx3: str,
    *,
    checks_mergeability: bool,
) -> None:
    """`do_multisig`'s own `assert_mergeability` block.

    :param outpoint: the multisig coin both partial signatures spend.
    :param out_spk: what the spend pays.
    :param rawtx2: the spend, signed by every key but the last.
    :param rawtx3: the spend, signed by the last key alone.
    :param checks_mergeability: whether the node is expected to refuse a
        transaction differing from the first, and fewer than two, in the
        wording Core's pinned file asserts -- where not, what a release
        before `v32.0` answers instead: the empty list refused in its
        own shorter wording, and a single transaction returned as it
        came.
    """
    combine = "combinerawtransaction"
    _refused(
        node, combine, [[rawtx2, rawtx3 + "00"]], _DESERIALIZATION, "TX decode failed"
    )
    if not checks_mergeability:
        with pytest.raises(RpcError) as refusal:
            node.rpc.call(combine, [[]])
        assert refusal.value.code == _DESERIALIZATION
        assert refusal.value.args[0].endswith(": Missing transactions")
        assert node.rpc.call(combine, [[rawtx2]]) == rawtx2
        return

    missing = "Missing transactions. At least two transactions required."
    _refused(node, combine, [[]], _DESERIALIZATION, missing)
    _refused(node, combine, [[rawtx2]], _DESERIALIZATION, missing)

    out_spk2 = _new_destination()
    unmergeable = [
        _unsigned(outpoint, out_spk, _OUT_VALUE * 2),
        _unsigned(outpoint, out_spk, _OUT_VALUE, version=1),
        _unsigned(outpoint, out_spk, _OUT_VALUE, lock_time=1),
        _unsigned(outpoint, out_spk2, _OUT_VALUE),
        _unsigned(outpoint, out_spk, _OUT_VALUE, sequence=1),
        _unsigned(OutPoint(outpoint.tx_id, outpoint.vout + 1), out_spk, _OUT_VALUE),
    ]
    for unrelated in unmergeable:
        _refused(
            node,
            combine,
            [[rawtx2, unrelated]],
            _INVALID_PARAMETER,
            "Transaction number 2 not compatible with first transaction",
        )

    assert node.rpc.call(combine, [[rawtx2, rawtx2]]) == rawtx2


def _do_multisig(
    node: NodeAdapter,
    wallet: MiniWallet,
    keys: tuple[list[str], list[str]],
    nsigs: int,
    output_type: str,
    *,
    checks_mergeability: bool | None = None,
) -> None:
    """Core's `do_multisig`, from the funding on.

    :param node: the node signing, combining and confirming.
    :param wallet: funds the multisig output and mines both blocks.
    :param keys: the public keys and the private keys, as `_keypairs`
        returns them.
    :param nsigs: the signatures required.
    :param output_type: `legacy`, `p2sh-segwit` or `bech32`.
    :param checks_mergeability: `None` where Core's `assert_mergeability`
        is false, and otherwise `_combinerawtransaction_refusals`' own.
    """
    pub_keys, priv_keys = keys
    redeem_script = multisig_script(nsigs, pub_keys)
    spk = multisig_script_pub_key(redeem_script, output_type)
    mredeem = redeem_script.hex()

    funding = wallet.send_to(spk, _VALUE)
    wallet.generate(1, confirm=[funding])
    outpoint = OutPoint(funding.id, 1)
    prevtxs = [
        {
            "txid": funding.id.hex(),
            "vout": outpoint.vout,
            "scriptPubKey": spk.script.hex(),
            "redeemScript": mredeem,
            "amount": _VALUE_BTC,
        }
    ]

    out_spk = _new_destination()
    rawtx = _unsigned(outpoint, out_spk, _OUT_VALUE)
    partial = priv_keys[: nsigs - 1]
    sign = "signrawtransactionwithkey"

    prevtx_err = dict(prevtxs[0])
    del prevtx_err["redeemScript"]
    _refused(
        node,
        sign,
        [rawtx, partial, [prevtx_err]],
        _INVALID_PARAMETER,
        "Missing redeemScript/witnessScript",
    )

    # witnessScript alone, or beside redeemScript, is accepted
    prevtx_err["witnessScript"] = mredeem
    node.rpc.call(sign, [rawtx, partial, [prevtx_err]])
    prevtx_err["redeemScript"] = mredeem
    node.rpc.call(sign, [rawtx, partial, [prevtx_err]])

    prevtx_err["redeemScript"] = "6a"  # OP_RETURN
    _refused(
        node,
        sign,
        [rawtx, partial, [prevtx_err]],
        _INVALID_PARAMETER,
        "redeemScript does not correspond to witnessScript",
    )
    del prevtx_err["witnessScript"]
    _refused(
        node,
        sign,
        [rawtx, partial, [prevtx_err]],
        _INVALID_PARAMETER,
        "redeemScript/witnessScript does not match scriptPubKey",
    )
    prevtx_err["witnessScript"] = prevtx_err.pop("redeemScript")
    _refused(
        node,
        sign,
        [rawtx, partial, [prevtx_err]],
        _INVALID_PARAMETER,
        "redeemScript/witnessScript does not match scriptPubKey",
    )

    rawtx2 = node.rpc.call(sign, [rawtx, partial, prevtxs])
    assert rawtx2["complete"] is False
    rawtx3 = node.rpc.call(sign, [rawtx, [priv_keys[-1]], prevtxs])
    assert rawtx3["complete"] is False

    if checks_mergeability is not None:
        _combinerawtransaction_refusals(
            node,
            outpoint,
            out_spk,
            rawtx2["hex"],
            rawtx3["hex"],
            checks_mergeability=checks_mergeability,
        )

    combine = "combinerawtransaction"
    combined = node.rpc.call(combine, [[rawtx2["hex"], rawtx3["hex"]]])
    spent = node.rpc.call("sendrawtransaction", [combined, 0])
    (block_hash,) = wallet.generate(1, confirm=[Tx.parse(combined)])
    assert spent in node.rpc.call("getblock", [block_hash.hex()])["tx"]

    _refused(
        node,
        combine,
        [[rawtx2["hex"], rawtx3["hex"]]],
        _VERIFY_ERROR,
        "Input not found or already spent",
    )


def _funded(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> tuple[NodeAdapter, MiniWallet]:
    """Start one node, check what the body asks of it, and mature coins."""
    (node,) = cluster(1)
    require(Capability.SIGN_RAW_TRANSACTION, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + len(M_OF_N) * len(OUTPUT_TYPES))
    return node, wallet


def every_multisig_spends_once_the_node_combines_its_signatures(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """`do_multisig` over every `(nsigs, nkeys, output_type)` Core's list names.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _funded(cluster, skip_counts)
    pub, priv = _keypairs(max(nkeys for _, nkeys in M_OF_N))
    for nsigs, nkeys in M_OF_N:
        for output_type in OUTPUT_TYPES:
            _do_multisig(node, wallet, (pub[:nkeys], priv[:nkeys]), nsigs, output_type)


def sixteen_of_twenty_spends_once_the_node_combines_its_signatures(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """`test_multisig_script_limit`'s own `do_multisig` calls: 16-of-20.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _funded(cluster, skip_counts)
    keys = _keypairs(20)
    for output_type in ("p2sh-segwit", "bech32"):
        _do_multisig(node, wallet, keys, 16, output_type)


def combinerawtransaction_preconditions(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    checks_mergeability: Callable[[NodeAdapter], bool],
) -> None:
    """Core's `test_combinerawtransaction_preconditions`: 2-of-3, `bech32`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    :param checks_mergeability: asked of the running node whether it is
        expected to refuse what Core's pinned file has it refuse.
    """
    node, wallet = _funded(cluster, skip_counts)
    pub, priv = _keypairs(3)
    _do_multisig(
        node,
        wallet,
        (pub, priv),
        2,
        "bech32",
        checks_mergeability=checks_mergeability(node),
    )
