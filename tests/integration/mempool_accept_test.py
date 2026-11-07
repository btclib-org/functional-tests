# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `mempool_accept`, as a body over each node.

Read from Core's `test/functional/mempool_accept.py` (`eaef8d31118d`,
2026-07-07): `testmempoolaccept` answers each raw transaction Core's
file builds -- garbage, one already known, ones that replace, spend
missing inputs, break a consensus rule, break a standardness rule, or
are standard at a policy boundary -- with Core's own verdict, reject
reason and fees, and leaves the mempool as it found it; and
`getmempoolinfo` reports the fee defaults and `-permitbaremultisig=0`.

Core's `run_test` is the body, in its own order, and every assertion of
Core's own is kept on a build with bitcoin/bitcoin#32406 and
bitcoin/bitcoin#29954. It asks for `Capability.PERMIT_BARE_MULTISIG` and
`Capability.MINE`, and restarts its node with the `-permitbaremultisig=0` Core
starts it with before any block is mined.

`testmempoolaccept` reports `vsize_adjusted` and `vsize_bip141` beside
`vsize` only in `v32.0` and later, from bitcoin/bitcoin#32800's merge.
Where the build's own `getnetworkinfo` `version` reads older
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)),
each allowed transaction is expected without them, as Core's file at
`v30.3` expects it. `_VSIZE_FIELDS_VERSION`'s own comment has the
known limit.

A build without bitcoin/bitcoin#32406, read by `mempool_datacarrier_test.py`'s
`uncapped_by_default`, relays one null-data output per transaction: there
the several-outputs, large-output and maximal-size `OP_RETURN` checks give
way to one, two outputs refused as `multi-op-return`. A build without
bitcoin/bitcoin#29954, read by `mempool_info_reports`, has no
`permitbaremultisig` in `getmempoolinfo`, so that assertion is skipped and the
`bare-multisig` refusal is the check there.

What differs from Core's file:

- the chain Core's framework caches is mined here by `MiniWallet`
  (`mini_wallet.py`), to the same height, every coinbase paying this
  wallet where Core's cache pays its own `MiniWallet` some of them; each
  coin the body spends has the value Core's has;
- a block Core's node mines from its own mempool is built here
  client-side, carrying the transactions Core's would have taken, in an
  order where each parent precedes its child: `MiniWallet.generate`'s
  own `confirm`, which also caches the coins they pay; and a block
  Core's `generateblock` mines is `build_next_block`'s, submitted over
  `submitblock`, after which the wallet resyncs its tip;
- a transaction Core sends through its `MiniWallet.sendrawtransaction`
  is sent over the node's own `sendrawtransaction` with `maxfeerate` 0,
  as Core's wallet sends it, and cached by the `generate` confirming it;
- Core's node runs with `-txindex` so that `getrawtransaction` finds
  the coinbase a transaction spends; here `getrawtransaction` is given
  that coinbase's block hash instead, which needs no index, and Core's
  `sync_txindex` has no index to wait for;
- a copy of an input that Core's serializer writes with an empty
  witness, its witness list holding fewer entries than the inputs, is
  given an empty witness here explicitly.

Each `mempool_accept_<node>_test.py` module beside this one runs the
body, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import math
import secrets
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError, RPCErrorCode
from btclib.consensus import MAX_BLOCK_WEIGHT, WITNESS_SCALE_FACTOR
from btclib.key import PrvKeyData
from btclib.script import sig_hash
from btclib.script.engine import PAY_TO_ANCHOR
from btclib.script.script import serialize as script_serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.script.witness import Witness
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import SEQUENCE_FINAL
from ecc.curves.curve import secp256k1
from ecc.ecc.dsa import sign_

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet, build_next_block
from tests.integration.mempool_datacarrier_test import (
    mempool_info_reports,
    uncapped_by_default,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["mempool_acceptance_of_raw_transactions"]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

# what Core's own `set_test_params` starts the node with, `-txindex`
# aside (this module's own docstring has why)
_NO_BARE_MULTISIG = "-permitbaremultisig=0"

# the height of the chain Core's own framework caches
_CHAIN_HEIGHT = 200

# satoshis per bitcoin, Core's own `COIN` (`test_framework/messages.py`)
_COIN = 100_000_000

# Core's own `MAX_MONEY` (`test_framework/messages.py`)
_MAX_MONEY = 21_000_000 * _COIN

# Core's own `MAX_BIP125_RBF_SEQUENCE` (`test_framework/messages.py`)
_MAX_BIP125_RBF_SEQUENCE = 0xFFFFFFFD

# Core's own `MAX_STANDARD_TX_WEIGHT` (`test_framework/blocktools.py`)
_MAX_STANDARD_TX_WEIGHT = 400_000

# Core's own `MIN_STANDARD_TX_NONWITNESS_SIZE` and `MIN_PADDING`
# (`test_framework/script_util.py`)
_MIN_STANDARD_TX_NONWITNESS_SIZE = 65
_MIN_PADDING = _MIN_STANDARD_TX_NONWITNESS_SIZE - 10 - 41 - 9

# Core's own `DEFAULT_MIN_RELAY_TX_FEE` and `DEFAULT_INCREMENTAL_RELAY_FEE`
# (`test_framework/mempool_util.py`), in satoshis per kvB
_DEFAULT_MIN_RELAY_TX_FEE = 100
_DEFAULT_INCREMENTAL_RELAY_FEE = 100

# Core's own `MAX_PACKAGE_COUNT` (`src/policy/packages.h`) plus one: the
# array `testmempoolaccept` refuses as too long
_TOO_MANY_TXS = 26

# Core's own `fee`, in satoshis: `Decimal('0.000007')` BTC
_FEE = 700

# Core's own value of each `PAY_TO_ANCHOR` output, in satoshis
_ANCHOR_VALUE = 10_000

# Core's own `op_return_count`, the `OP_RETURN` outputs of one standard
# transaction
_OP_RETURN_COUNT = 42

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which `testmempoolaccept`
# reports `vsize_adjusted` and `vsize_bip141`: `32.0`'s, the first release
# carrying bitcoin/bitcoin#32800 (`6b059d9dbd`, merged 2026-07-24 while
# `master` read `31.99`). `31.99`'s own, which `master` reached earlier
# (`b97abdcdf1`, 2026-03-10), is not the threshold because it leaves the
# longer window this test is wrong in. A known limit: a `master` build from
# that merge up to the move to `32.99` (`f3fec67c3e`, 2026-09-11) reports
# `319900` with the fields, so this test fails against such a build
_VSIZE_FIELDS_VERSION = 320000

_OP_TRUE = b"\x51"
_P2WSH_OP_TRUE = ScriptPubKey.p2wsh(_OP_TRUE)


def _hex(tx: Tx) -> str:
    """Return `tx` serialized with its witness, Core's `serialize().hex()`."""
    return tx.serialize(True, check_validity=False).hex()


def _parse(tx_hex: str) -> Tx:
    """Return a fresh `Tx` decoded from `tx_hex`, Core's `tx_from_hex`."""
    return Tx.parse(bytes.fromhex(tx_hex), check_validity=False)


def _out(value: int, script: bytes | ScriptPubKey) -> TxOut:
    """Return an output Core's `CTxOut` builds, a valid amount or not."""
    if not isinstance(script, ScriptPubKey):
        script = ScriptPubKey(script, check_validity=False)
    return TxOut(value, script, check_validity=False)


def _in(prev_out: OutPoint, script_sig: bytes = b"", sequence: int = 0) -> TxIn:
    """Return an input with an empty witness, Core's `CTxIn`."""
    return TxIn(prev_out, script_sig, sequence, Witness(), check_validity=False)


def _outpoint(txid: str, vout: int) -> OutPoint:
    """Return Core's `COutPoint(int(txid, 16), vout)`."""
    return OutPoint(bytes.fromhex(txid), vout, check_validity=False)


def _reports_vsize_fields(node: NodeAdapter) -> bool:
    """Whether `node`'s `testmempoolaccept` reports the two newer vsizes.

    Core's own claim for every node, bitcoind before
    `_VSIZE_FIELDS_VERSION` excepted, read off its own `getnetworkinfo`
    `version`
    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
    """
    if not isinstance(node, BitcoindAdapter):
        return True
    version: int = node.rpc.call("getnetworkinfo")["version"]
    return version >= _VSIZE_FIELDS_VERSION


class _Mempool:
    """Core's `check_mempool_result`, over one node, and its `mempool_size`."""

    def __init__(self, node: NodeAdapter) -> None:
        self.node = node
        self.size = 0
        self._vsize_fields = _reports_vsize_fields(node)

    def allowed(self, tx: Tx, base_fee: int) -> dict[str, object]:
        """Return the entry Core expects for `tx` allowed, paying `base_fee`."""
        entry: dict[str, object] = {"txid": tx.id.hex(), "allowed": True}
        if self._vsize_fields:
            entry["vsize_adjusted"] = tx.vsize
        entry["vsize"] = tx.vsize
        if self._vsize_fields:
            entry["vsize_bip141"] = tx.vsize
        entry["fees"] = {"base": Decimal(base_fee) / _COIN}
        return entry

    @staticmethod
    def refused(tx: Tx | str, reason: str) -> dict[str, object]:
        """Return the entry Core expects for `tx`, or its txid, refused."""
        txid = tx if isinstance(tx, str) else tx.id.hex()
        return {"txid": txid, "allowed": False, "reject-reason": reason}

    def check(
        self,
        expected: dict[str, object],
        tx: Tx | str,
        maxfeerate: float | None = None,
    ) -> None:
        """Assert `testmempoolaccept`'s answer for `tx` and the mempool's size.

        Core's `check_mempool_result`: `wtxid`, the fees' own
        `effective-feerate` and `effective-includes`, and
        `reject-details` are dropped before the comparison, as Core
        drops them, and the mempool must still hold `size` transactions.
        """
        tx_hex = tx if isinstance(tx, str) else _hex(tx)
        params: list[object] = [[tx_hex]]
        if maxfeerate is not None:
            params.append(maxfeerate)
        result = self.node.rpc.call("testmempoolaccept", params)
        for entry in result:
            entry.pop("wtxid")
            if "fees" in entry:
                entry["fees"].pop("effective-feerate")
                entry["fees"].pop("effective-includes")
            entry.pop("reject-details", None)
        assert result == [expected]
        assert self.node.rpc.call("getmempoolinfo")["size"] == self.size


def _assert_rpc_error(
    node: NodeAdapter, code: int, message: str, method: str, params: list[object]
) -> None:
    """Core's `assert_raises_rpc_error`: the code, and the message within."""
    with pytest.raises(RpcError) as refused:
        node.rpc.call(method, params)
    assert refused.value.code == code
    assert message in refused.value.args[0]


def _generateblock(node: NodeAdapter, wallet: MiniWallet, txs: Sequence[Tx]) -> None:
    """Core's `generateblock` to the wallet's address, carrying `txs`."""
    block = build_next_block(node, wallet.script_pub_key, txs)
    answer = node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    assert answer is None
    wallet.resync()


def _garbage_is_refused(node: NodeAdapter) -> None:
    """Core's "Should not accept garbage to testmempoolaccept"."""
    _assert_rpc_error(
        node,
        RPCErrorCode.TYPE_ERROR,
        "JSON value of type string is not of expected type array",
        "testmempoolaccept",
        ["ff00baar"],
    )
    _assert_rpc_error(
        node,
        RPCErrorCode.INVALID_PARAMETER,
        "Array must contain between 1 and 25 transactions.",
        "testmempoolaccept",
        [["ff22"] * _TOO_MANY_TXS],
    )
    _assert_rpc_error(
        node,
        RPCErrorCode.INVALID_PARAMETER,
        "Array must contain between 1 and 25 transactions.",
        "testmempoolaccept",
        [[]],
    )
    _assert_rpc_error(
        node,
        RPCErrorCode.DESERIALIZATION_ERROR,
        "TX decode failed",
        "testmempoolaccept",
        [["ff00baar"]],
    )


def _op_return_outputs_are_standard(mempool: _Mempool, raw_tx_reference: str) -> None:
    """Core's checks of several and of large `OP_RETURN` outputs."""
    # Multiple OP_RETURN and more than 83 bytes, even if over
    # MAX_SCRIPT_ELEMENT_SIZE, are standard since v30
    tx = _parse(raw_tx_reference)
    tx.vout.append(_out(0, script_serialize(["OP_RETURN", b"\xff"])))
    tx.vout.append(_out(0, script_serialize(["OP_RETURN", b"\xff" * 50_000])))
    mempool.check(mempool.allowed(tx, _COIN // 20), tx, maxfeerate=0)

    # A transaction with several OP_RETURN outputs: Core's
    # `int(nValue / op_return_count)`, one object repeated
    tx = _parse(raw_tx_reference)
    share = int(tx.vout[0].value / _OP_RETURN_COUNT)
    tx.vout = [_out(share, script_serialize(["OP_RETURN", b"\xff"]))] * _OP_RETURN_COUNT
    # Core's own `Decimal("0.05000026")`: the reference's input less the
    # outputs' own sum
    mempool.check(mempool.allowed(tx, 5_000_026), tx)

    # A transaction with an OP_RETURN output that bumps into the max
    # standardness tx size: -5 for PUSHDATA4 and -4 for script size
    tx = _parse(raw_tx_reference)
    tx.vout[0] = _out(tx.vout[0].value, script_serialize(["OP_RETURN"]))
    data_len = _MAX_STANDARD_TX_WEIGHT // 4 - tx.vsize - 5 - 4
    value = tx.vout[0].value
    tx.vout[0] = _out(value, script_serialize(["OP_RETURN", b"\xff" * data_len]))
    assert tx.vsize == _MAX_STANDARD_TX_WEIGHT // 4
    mempool.check(mempool.allowed(tx, _COIN // 10 - _COIN // 20), tx)
    tx.vout[0] = _out(value, script_serialize(["OP_RETURN", b"\xff" * (data_len + 1)]))
    assert tx.vsize > _MAX_STANDARD_TX_WEIGHT // 4
    mempool.check(mempool.refused(tx, "tx-size"), tx)


def _reference_variants_are_refused(
    mempool: _Mempool, raw_tx_reference: str, raw_tx_coinbase_spent: str
) -> None:
    """Core's checks on variants of its "reference" tx, up to the timelocks."""
    node = mempool.node

    # A transaction with no outputs
    tx = _parse(raw_tx_reference)
    tx.vout = []
    mempool.check(mempool.refused(tx, "bad-txns-vout-empty"), tx)

    # A really large transaction: Core's copies of the input past the
    # first carry no witness of their own
    tx = _parse(raw_tx_reference)
    input_size = len(tx.vin[0].serialize(check_validity=False))
    copies = math.ceil((MAX_BLOCK_WEIGHT // WITNESS_SCALE_FACTOR) / input_size)
    prev = tx.vin[0]
    tx.vin = [prev] + [
        _in(prev.prev_out, prev.script_sig, prev.sequence) for _ in range(copies - 1)
    ]
    mempool.check(mempool.refused(tx, "bad-txns-oversize"), tx)

    # A transaction with negative output value
    tx = _parse(raw_tx_reference)
    tx.vout[0] = _out(-tx.vout[0].value, tx.vout[0].script_pub_key)
    mempool.check(mempool.refused(tx, "bad-txns-vout-negative"), tx)

    # A transaction with too large output value
    tx = _parse(raw_tx_reference)
    tx.vout[0] = _out(_MAX_MONEY + 1, tx.vout[0].script_pub_key)
    mempool.check(mempool.refused(tx, "bad-txns-vout-toolarge"), tx)

    # A transaction with too large sum of output values: Core's two
    # outputs are one object, so both carry `MAX_MONEY`
    tx = _parse(raw_tx_reference)
    tx.vout = [_out(_MAX_MONEY, tx.vout[0].script_pub_key)] * 2
    mempool.check(mempool.refused(tx, "bad-txns-txouttotal-toolarge"), tx)

    # A transaction with duplicate inputs
    tx = _parse(raw_tx_reference)
    prev = tx.vin[0]
    tx.vin = [prev, _in(prev.prev_out, prev.script_sig, prev.sequence)]
    mempool.check(mempool.refused(tx, "bad-txns-inputs-duplicate"), tx)

    # A non-coinbase transaction with coinbase-like outpoint
    tx = _parse(raw_tx_reference)
    tx.vin.append(_in(OutPoint(b"\x00" * 32, 0xFFFFFFFF, check_validity=False)))
    mempool.check(mempool.refused(tx, "bad-txns-prevout-null"), tx)

    # A coinbase transaction
    tx = _parse(raw_tx_coinbase_spent)
    mempool.check(mempool.refused(tx, "coinbase"), tx)

    # Some nonstandard transactions
    tx = _parse(raw_tx_reference)
    tx.version = 4
    mempool.check(mempool.refused(tx, "version"), tx)

    tx = _parse(raw_tx_reference)
    tx.vout[0] = _out(tx.vout[0].value, script_serialize(["OP_0"]))
    mempool.check(mempool.refused(tx, "scriptpubkey"), tx)

    tx = _parse(raw_tx_reference)
    pubkey = PrvKeyData(1 + secrets.randbelow(secp256k1.n - 1), network="regtest").pub
    tx.vout[0] = _out(tx.vout[0].value, ScriptPubKey.p2ms(2, [pubkey] * 3))
    mempool.check(mempool.refused(tx, "bare-multisig"), tx)

    tx = _parse(raw_tx_reference)
    tx.vin[0].script_sig = script_serialize(["OP_HASH160"])
    mempool.check(mempool.refused(tx, "scriptsig-not-pushonly"), tx)

    tx = _parse(raw_tx_reference)
    tx.vin[0].script_sig = script_serialize([b"a" * 1648])
    mempool.check(mempool.refused(tx, "scriptsig-size"), tx)

    tx = _parse(raw_tx_reference)
    p2sh_burn = ScriptPubKey.p2sh(b"burn", check_validity=False)
    output_p2sh_burn = _out(540, p2sh_burn)
    num_scripts = 100_000 // len(output_p2sh_burn.serialize(check_validity=False))
    tx.vout = [output_p2sh_burn] * num_scripts
    mempool.check(mempool.refused(tx, "tx-size"), tx)

    # Core's `nValue -= 1` on the output it just used above
    tx = _parse(raw_tx_reference)
    tx.vout[0] = _out(540 - 1, p2sh_burn)
    mempool.check(mempool.refused(tx, "dust"), tx)

    # OP_RETURN followed by non-push
    tx = _parse(raw_tx_reference)
    tx.vout[0] = _out(tx.vout[0].value, script_serialize(["OP_RETURN", "OP_HASH160"]))
    mempool.check(mempool.refused(tx, "scriptpubkey"), tx)

    if uncapped_by_default(node):
        _op_return_outputs_are_standard(mempool, raw_tx_reference)
    else:
        # Core's own check on `v29.4`: one null-data output per transaction
        tx = _parse(raw_tx_reference)
        tx.vout = [_out(tx.vout[0].value, script_serialize(["OP_RETURN", b"\xff"]))] * 2
        mempool.check(mempool.refused(tx, "multi-op-return"), tx)

    # A timelocked transaction: non-max, so the locktime is not ignored
    tx = _parse(raw_tx_reference)
    tx.vin[0].sequence -= 1
    tx.lock_time = node.rpc.call("getblockcount") + 1
    mempool.check(mempool.refused(tx, "non-final"), tx)

    # A transaction that is locked by BIP68 sequence logic: includable in
    # the second block mined from now, but not the very next one
    tx = _parse(raw_tx_reference)
    tx.vin[0].sequence = 2
    mempool.check(mempool.refused(tx, "non-BIP68-final"), tx, maxfeerate=0)


def _tiny_and_anchor_spends(mempool: _Mempool, wallet: MiniWallet) -> None:
    """Core's tiny-tx and `PAY_TO_ANCHOR` checks, in its order."""
    node = mempool.node

    # Prep for tiny-tx tests with wsh(OP_TRUE) output
    seed_tx = wallet.send_to(_P2WSH_OP_TRUE, _COIN)
    wallet.generate(1, confirm=[seed_tx])

    # A tiny transaction (in non-witness bytes) that is disallowed
    tx = Tx(
        version=2,
        lock_time=0,
        vin=[
            TxIn(
                OutPoint(seed_tx.id, 1, check_validity=False),
                b"",
                SEQUENCE_FINAL,
                Witness([_OP_TRUE]),
                check_validity=False,
            )
        ],
        vout=[_out(0, script_serialize(["OP_RETURN"] + ["OP_0"] * (_MIN_PADDING - 2)))],
        check_validity=False,
    )
    # only the non-witness size matters
    non_witness_size = len(tx.serialize(False, check_validity=False))
    assert non_witness_size == 64
    assert _MIN_STANDARD_TX_NONWITNESS_SIZE == 65
    assert len(tx.serialize(True, check_validity=False)) > 64
    mempool.check(mempool.refused(tx, "tx-size-small"), tx, maxfeerate=0)

    # Minimally-small transaction (in non-witness bytes) that is allowed:
    # Core's `DUMMY_MIN_OP_RETURN_SCRIPT` (`test_framework/script_util.py`)
    dummy_min_op_return = script_serialize(
        ["OP_RETURN"] + ["OP_0"] * (_MIN_PADDING - 1)
    )
    tx.vout[0] = _out(_COIN - 1000, dummy_min_op_return)
    non_witness_size = len(tx.serialize(False, check_validity=False))
    assert non_witness_size == _MIN_STANDARD_TX_NONWITNESS_SIZE
    mempool.check(mempool.allowed(tx, 1000), tx, maxfeerate=0)

    # OP_1 <0x4e73> is able to be created and spent
    create_anchor_tx = wallet.send_to(ScriptPubKey(PAY_TO_ANCHOR), _ANCHOR_VALUE)
    wallet.generate(1, confirm=[create_anchor_tx])

    # a first spend with a non-empty witness is refused, to prevent third
    # party wtxid malleability
    anchor_nonempty_wit_spend = Tx(
        version=2,
        lock_time=0,
        vin=[
            TxIn(
                OutPoint(create_anchor_tx.id, 1, check_validity=False),
                b"",
                0,
                Witness([b"f"]),
                check_validity=False,
            )
        ],
        vout=[_out(_ANCHOR_VALUE - _FEE, _P2WSH_OP_TRUE)],
        check_validity=False,
    )
    mempool.check(
        mempool.refused(anchor_nonempty_wit_spend, "bad-witness-nonstandard"),
        anchor_nonempty_wit_spend,
        maxfeerate=0,
    )
    # but is consensus-legal
    _generateblock(node, wallet, [anchor_nonempty_wit_spend])

    # Without witness elements it is standard
    create_anchor_tx = wallet.send_to(ScriptPubKey(PAY_TO_ANCHOR), _ANCHOR_VALUE)
    wallet.generate(1, confirm=[create_anchor_tx])
    anchor_spend = Tx(
        version=2,
        lock_time=0,
        vin=[_in(OutPoint(create_anchor_tx.id, 1, check_validity=False))],
        vout=[_out(_ANCHOR_VALUE - _FEE, _P2WSH_OP_TRUE)],
        check_validity=False,
    )
    # "segwit", but txid == wtxid since there is no witness data
    assert anchor_spend.id == anchor_spend.hash
    mempool.check(mempool.allowed(anchor_spend, _FEE), anchor_spend, maxfeerate=0)

    # But cannot be spent if nested sh()
    nested_anchor_tx = wallet.create_self_transfer(sequence=SEQUENCE_FINAL)
    p2sh_anchor = ScriptPubKey.p2sh(PAY_TO_ANCHOR, check_validity=False)
    nested_anchor_tx.vout[0] = _out(nested_anchor_tx.vout[0].value, p2sh_anchor)
    _generateblock(node, wallet, [nested_anchor_tx])
    nested_anchor_spend = Tx(
        version=2,
        lock_time=0,
        vin=[
            _in(
                OutPoint(nested_anchor_tx.id, 0, check_validity=False),
                script_serialize([PAY_TO_ANCHOR]),
            )
        ],
        vout=[_out(nested_anchor_tx.vout[0].value - _FEE, _P2WSH_OP_TRUE)],
        check_validity=False,
    )
    reason = (
        "mempool-script-verify-flag-failed "
        "(Witness version reserved for soft-fork upgrades)"
    )
    mempool.check(
        mempool.refused(nested_anchor_spend, reason), nested_anchor_spend, maxfeerate=0
    )
    # but is consensus-legal
    _generateblock(node, wallet, [nested_anchor_spend])


def _confirmed_bare_multisig_is_spent(
    mempool: _Mempool, wallet: MiniWallet, raw_tx_reference: str
) -> None:
    """Core's "Spending a confirmed bare multisig is okay"."""
    tx = _parse(raw_tx_reference)
    privkey = PrvKeyData(1 + secrets.randbelow(secp256k1.n - 1), network="regtest")
    # Some bare multisig script (1-of-3)
    multisig = ScriptPubKey.p2ms(1, [privkey.pub] * 3)
    tx.vout[0] = _out(tx.vout[0].value, multisig)
    _generateblock(mempool.node, wallet, [tx])
    tx_spend = Tx(
        version=2,
        lock_time=0,
        vin=[_in(OutPoint(tx.id, 0, check_validity=False))],
        vout=[_out(tx.vout[0].value - _FEE, _P2WSH_OP_TRUE)],
        check_validity=False,
    )
    # Core's `sign_input_legacy`, then `OP_0` prepended for the dummy
    digest = sig_hash.legacy(multisig.script, tx_spend, 0, sig_hash.ALL)
    signature = sign_(digest, privkey.q).serialize() + bytes([sig_hash.ALL])
    tx_spend.vin[0].script_sig = script_serialize(["OP_0", signature])
    mempool.check(mempool.allowed(tx_spend, _FEE), tx_spend, maxfeerate=0)


def mempool_acceptance_of_raw_transactions(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.PERMIT_BARE_MULTISIG, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart([_NO_BARE_MULTISIG])
    wallet = MiniWallet(node)
    wallet.generate(_CHAIN_HEIGHT)
    mempool = _Mempool(node)

    if mempool_info_reports(node, "permitbaremultisig"):
        assert node.rpc.call("getmempoolinfo")["permitbaremultisig"] is False

    # Start with empty mempool, and 200 blocks
    assert node.rpc.call("getblockcount") == _CHAIN_HEIGHT
    assert node.rpc.call("getmempoolinfo")["size"] == mempool.size

    # Check default settings, listed in BTC/kvB
    info = node.rpc.call("getmempoolinfo")
    assert info["minrelaytxfee"] == Decimal(_DEFAULT_MIN_RELAY_TX_FEE) / _COIN
    assert (
        info["incrementalrelayfee"] == Decimal(_DEFAULT_INCREMENTAL_RELAY_FEE) / _COIN
    )

    _garbage_is_refused(node)

    # A transaction already in the blockchain, spending a coinbase
    coinbase_utxo = wallet.get_utxo()
    tx = wallet.create_self_transfer(utxo_to_spend=coinbase_utxo)
    tx.vout.append(tx.vout[0])
    tx.vout[0] = _out(int(0.3 * _COIN), tx.vout[0].script_pub_key)
    tx.vout[1] = _out(49 * _COIN, tx.vout[1].script_pub_key)
    tx_in_block = tx
    raw_tx_in_block = _hex(tx)
    txid_in_block = node.rpc.call("sendrawtransaction", [raw_tx_in_block, 0])
    wallet.generate(1, confirm=[tx_in_block])
    mempool.size = 0
    # Also check feerate. 1BTC/kvB fails
    _assert_rpc_error(
        node,
        RPCErrorCode.INVALID_PARAMETER,
        "Fee rates larger than or equal to 1BTC/kvB are not accepted",
        "testmempoolaccept",
        [[raw_tx_in_block], 1],
    )
    # Check negative feerate
    _assert_rpc_error(
        node,
        RPCErrorCode.TYPE_ERROR,
        "Amount out of range",
        "testmempoolaccept",
        [[raw_tx_in_block], -0.01],
    )
    # ... 0.99 passes
    mempool.check(
        mempool.refused(txid_in_block, "txn-already-known"),
        raw_tx_in_block,
        maxfeerate=0.99,
    )

    # A transaction not in the mempool, spending the 0.3 BTC output
    utxo_to_spend = wallet.get_utxo(txid=txid_in_block)
    tx = wallet.create_self_transfer(
        utxo_to_spend=utxo_to_spend, sequence=_MAX_BIP125_RBF_SEQUENCE
    )
    tx.vout[0] = _out(int(0.3 * _COIN) - _FEE, tx.vout[0].script_pub_key)
    raw_tx_0 = _hex(tx)
    txid_0 = tx.id.hex()
    mempool.check(mempool.allowed(tx, _FEE), tx)

    # A final transaction not in the mempool, its locktime anything
    output_amount = _COIN // 40
    tx = wallet.create_self_transfer(
        sequence=SEQUENCE_FINAL, locktime=node.rpc.call("getblockcount") + 2000
    )
    tx.vout[0] = _out(output_amount, tx.vout[0].script_pub_key)
    raw_tx_final = _hex(tx)
    tx_final = _parse(raw_tx_final)
    fee_expected = 50 * _COIN - output_amount
    mempool.check(mempool.allowed(tx_final, fee_expected), tx_final, maxfeerate=0)
    node.rpc.call("sendrawtransaction", [raw_tx_final, 0])
    mempool.size += 1

    # A transaction in the mempool
    node.rpc.call("sendrawtransaction", [raw_tx_0])
    mempool.size += 1
    mempool.check(mempool.refused(txid_0, "txn-already-in-mempool"), raw_tx_0)

    # A transaction that replaces a mempool transaction: double the fee
    tx = _parse(raw_tx_0)
    tx.vout[0] = _out(tx.vout[0].value - _FEE, tx.vout[0].script_pub_key)
    raw_tx_0 = _hex(tx)
    txid_0 = tx.id.hex()
    tx_0 = tx
    mempool.check(mempool.allowed(tx, 2 * _FEE), tx)
    node.rpc.call("sendrawtransaction", [raw_tx_0, 0])

    # A transaction with missing inputs, that never existed
    tx = _parse(raw_tx_0)
    tx.vin[0].prev_out = _outpoint("ff" * 32, 14)
    mempool.check(mempool.refused(tx, "missing-inputs"), tx)

    # A transaction with missing inputs, that existed once in the past:
    # vout 1 spends the other outpoint (49 coins) of the in-chain tx
    tx = _parse(raw_tx_0)
    tx.vin[0].prev_out = _outpoint(txid_in_block, 1)
    raw_tx_1 = _hex(tx)
    tx_1 = tx
    txid_1 = node.rpc.call("sendrawtransaction", [raw_tx_1, 0])
    # Now spend both to "clearly hide" the outputs, ie. remove the coins
    # from the utxo set by spending them
    tx = wallet.create_self_transfer()
    prev = tx.vin[0]
    tx.vin = [
        TxIn(_outpoint(txid_0, 0), prev.script_sig, prev.sequence, prev.script_witness),
        TxIn(_outpoint(txid_1, 0), prev.script_sig, prev.sequence, prev.script_witness),
    ]
    tx.vout[0] = _out(_COIN // 10, tx.vout[0].script_pub_key)
    raw_tx_spend_both = _hex(tx)
    txid_spend_both = node.rpc.call("sendrawtransaction", [raw_tx_spend_both, 0])
    wallet.generate(1, confirm=[tx_final, tx_0, tx_1, tx])
    mempool.size = 0
    # Now see if we can add the coins back to the utxo set by sending the
    # exact txs again
    mempool.check(mempool.refused(txid_0, "missing-inputs"), raw_tx_0)
    mempool.check(mempool.refused(txid_1, "missing-inputs"), raw_tx_1)

    # Create a "reference" tx for later use
    utxo_to_spend = wallet.get_utxo(txid=txid_spend_both)
    tx = wallet.create_self_transfer(
        utxo_to_spend=utxo_to_spend, sequence=SEQUENCE_FINAL
    )
    tx.vout[0] = _out(_COIN // 20, tx.vout[0].script_pub_key)
    raw_tx_reference = _hex(tx)
    # Reference tx should be valid on itself
    mempool.check(mempool.allowed(tx, _COIN // 10 - _COIN // 20), tx, maxfeerate=0)

    # Pick the input of the first tx created, so it has to be a coinbase
    # tx: its own block's hash stands in for Core's `-txindex`
    decoded = node.rpc.call("decoderawtransaction", [raw_tx_in_block])
    coinbase_txid = decoded["vin"][0]["txid"]
    block_hash = node.rpc.call("getblockhash", [coinbase_utxo.height])
    raw_tx_coinbase_spent = node.rpc.call(
        "getrawtransaction", [coinbase_txid, False, block_hash]
    )

    _reference_variants_are_refused(mempool, raw_tx_reference, raw_tx_coinbase_spent)
    _tiny_and_anchor_spends(mempool, wallet)
    _confirmed_bare_multisig_is_spent(mempool, wallet, raw_tx_reference)
