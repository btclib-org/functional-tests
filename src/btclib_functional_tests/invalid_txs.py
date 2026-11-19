# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's invalid-transaction templates, built with btclib as data.

Read from Core's `test/functional/data/invalid_txs.py`, at the pin of
the `feature_block.py` row of `TF2.md`'s per-test ledger. `TEMPLATES`
holds Core's templates in the order Core's `iter_all_templates` gives
them. Each builds a transaction from a spend transaction, `spend_tx`,
and says what a node makes of it: the reason the node refuses it with,
and whether a block may hold it. Nothing here reaches a node;
`feature_block.steps` puts each template a block may not hold in a block
of its own.

A template builds its transaction unsigned. Where the coin it spends
needs a signature, the caller signs input 0, as Core's `feature_block`
does.

What differs from Core's file: a template is given `spend_tx`, where
Core's may be given a block instead, to take its coinbase.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from btclib.block.limits import MAX_BLOCK_SIGOPS_COST, WITNESS_SCALE_FACTOR
from btclib.policy import MAX_STANDARD_TX_SIGOPS_COST, MIN_STANDARD_TX_NONWITNESS_SIZE
from btclib.script.script import BYTE_FROM_OP_CODE_NAME
from btclib.script.script import serialize as script_serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.script.witness import Witness
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import SEQUENCE_FINAL

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["TEMPLATES", "BadTxTemplate"]

# Core's own `CTransaction` default
_TX_VERSION = 2

# Core's own `COIN` and `MAX_MONEY` (`test_framework/messages.py`): btclib
# publishes no name for either
_COIN = 100_000_000
_MAX_MONEY = 21_000_000 * _COIN

# Core's own `MAX_BLOCK_SIGOPS` and `MAX_STANDARD_TX_SIGOPS`
# (`test_framework/blocktools.py`): the bounds in legacy sigops, each
# costing `WITNESS_SCALE_FACTOR`
_MAX_BLOCK_SIGOPS = MAX_BLOCK_SIGOPS_COST // WITNESS_SCALE_FACTOR
_MAX_STANDARD_TX_SIGOPS = MAX_STANDARD_TX_SIGOPS_COST // WITNESS_SCALE_FACTOR

# Core's own `MIN_PADDING` (`test_framework/script_util.py`): the octets
# an output script needs for a transaction of one input with an empty
# scriptSig and one output to reach `MIN_STANDARD_TX_NONWITNESS_SIZE`, past
# the 10 of its skeleton, the 41 of its input and the 9 of its output
_MIN_PADDING = MIN_STANDARD_TX_NONWITNESS_SIZE - 10 - 41 - 9

# Core's own `basic_p2sh`, the P2SH script of a script holding `OP_0`
_BASIC_P2SH = ScriptPubKey.p2sh(script_serialize(["OP_0"]), "regtest").script

_OP_TRUE = script_serialize(["OP_TRUE"])
_OP_CHECKSIG = BYTE_FROM_OP_CODE_NAME["OP_CHECKSIG"]
_OP_ENDIF = BYTE_FROM_OP_CODE_NAME["OP_ENDIF"]
_OP_CODESEPARATOR = BYTE_FROM_OP_CODE_NAME["OP_CODESEPARATOR"]
_OP_RETURN = BYTE_FROM_OP_CODE_NAME["OP_RETURN"]

# the op codes CVE-2010-5137 disabled, in Core's order
_DISABLED_OP_CODES = (
    "OP_CAT",
    "OP_SUBSTR",
    "OP_LEFT",
    "OP_RIGHT",
    "OP_INVERT",
    "OP_AND",
    "OP_OR",
    "OP_XOR",
    "OP_2MUL",
    "OP_2DIV",
    "OP_MUL",
    "OP_DIV",
    "OP_MOD",
    "OP_LSHIFT",
    "OP_RSHIFT",
)


@dataclass(frozen=True, kw_only=True)
class BadTxTemplate:
    """One kind of invalid transaction, and what a node makes of it.

    A reason is Core's, and may be a part of the node's own message, as
    the disabled op codes' `disabled opcode` is.

    :param name: Core's own name for the template, `OutputMissing` or
        `DisabledOpcode_OP_CAT`.
    :param get_tx: returns the transaction, unsigned, built from
        `spend_tx`, whose output 0 is the coin a valid input spends.
    :param reject_reason: the reason the node refuses the transaction
        with, in Core's words; `None` where the node keeps it as an
        orphan.
    :param std_reject_reason: the reason under `-acceptnonstdtxn=0`,
        where Core gives one apart from `reject_reason`.
    :param block_reject_reason: the reason the node refuses a block
        holding the transaction with, where Core gives one apart from
        `reject_reason`. Where it gives none and `valid_in_block` is
        false, a block is refused with `reject_reason`, read with
        `block-script-verify-flag-failed` in place of
        `mempool-script-verify-flag-failed`, as `feature_block.steps`
        reads it.
    :param valid_in_block: the transaction breaks policy alone, so a
        block may hold it.
    """

    name: str
    get_tx: Callable[[Tx], Tx]
    reject_reason: str | None
    std_reject_reason: str | None = None
    block_reject_reason: str = ""
    valid_in_block: bool = False


def _valid_txin(spend_tx: Tx, script_sig: bytes = b"") -> TxIn:
    """Core's own `valid_txin`: output 0 of `spend_tx`."""
    return TxIn(
        OutPoint(spend_tx.id, 0), script_sig, SEQUENCE_FINAL, check_validity=False
    )


def _tx(vin: list[TxIn], vout: list[TxOut]) -> Tx:
    """Return Core's own default `CTransaction`, of these inputs and outputs."""
    return Tx(_TX_VERSION, 0, vin, vout, check_validity=False)


def _out(value: int, script: bytes = b"") -> TxOut:
    """Return an output, built without checks."""
    return TxOut(value, script, check_validity=False)


def _spend_avail(spend_tx: Tx) -> int:
    """Core's own `spend_avail`: what every output of `spend_tx` holds."""
    return sum(tx_out.value for tx_out in spend_tx.vout)


def _output_missing(spend_tx: Tx) -> Tx:
    """Core's own `OutputMissing`."""
    return _tx([_valid_txin(spend_tx)], [])


def _input_missing(_spend_tx: Tx) -> Tx:
    """Core's own `InputMissing`: blank, so that its input count refuses it.

    Core's comment: a transaction with a witness would fail its
    superfluous witness check as it is read, before its input count is.
    """
    return _tx([], [])


def _size_too_small(spend_tx: Tx) -> Tx:
    """Core's own `SizeTooSmall`: 64 octets without its witness.

    The merkle tree does not commit to its depth (CVE-2017-12842), and a
    transaction of 64 octets can pass for an inner node of it.
    """
    script = script_serialize(["OP_RETURN"] + ["OP_0"] * (_MIN_PADDING - 2))
    return _tx([_valid_txin(spend_tx)], [_out(0, script)])


def _extra_witness(spend_tx: Tx) -> Tx:
    """Core's own `ExtraWitness`: a witness where no segwit output is spent."""
    vin = TxIn(
        OutPoint(spend_tx.id, 0),
        b"",
        SEQUENCE_FINAL,
        Witness([_OP_TRUE]),
        check_validity=False,
    )
    return _tx([vin], [_out(0)])


def _bad_input_outpoint_index(spend_tx: Tx) -> Tx:
    """Core's own `BadInputOutpointIndex`: output `len(spend_tx.vin) + 100`.

    Core counts the inputs of `spend_tx`, not its outputs, so the index is
    past them only where `spend_tx` has at most that many outputs. The
    mempool keeps it as an orphan: the coins database cannot tell a spent
    output from one that never existed.
    """
    bad_index = len(spend_tx.vin) + 100
    vin = TxIn(
        OutPoint(spend_tx.id, bad_index), b"", SEQUENCE_FINAL, check_validity=False
    )
    return _tx([vin], [_out(0, _BASIC_P2SH)])


def _duplicate_input(spend_tx: Tx) -> Tx:
    """Core's own `DuplicateInput`: the same input twice."""
    return _tx([_valid_txin(spend_tx)] * 2, [_out(1, _BASIC_P2SH)])


def _prevout_null_input(spend_tx: Tx) -> Tx:
    """Core's own `PrevoutNullInput`: a coinbase's input, in second place."""
    null = TxIn(OutPoint(), b"", 0, check_validity=False)
    return _tx([_valid_txin(spend_tx), null], [_out(1, _BASIC_P2SH)])


def _nonexistent_input(spend_tx: Tx) -> Tx:
    """Core's own `NonexistentInput`: first an input no transaction creates.

    Its id is Core's `txid_int + 1`, `spend_tx`'s id read as a number,
    plus one.
    """
    tx_id = (int.from_bytes(spend_tx.id, "big") + 1).to_bytes(32, "big")
    missing = TxIn(OutPoint(tx_id, 0), b"", SEQUENCE_FINAL, check_validity=False)
    return _tx([missing, _valid_txin(spend_tx)], [_out(1, _BASIC_P2SH)])


def _create_tx_with_script(
    spend_tx: Tx, amount: int, *, script_sig: bytes = b"", output_script: bytes = b""
) -> Tx:
    """Core's own `create_tx_with_script(spend_tx, 0, ...)`."""
    return _tx([_valid_txin(spend_tx, script_sig)], [_out(amount, output_script)])


def _spend_too_much(spend_tx: Tx) -> Tx:
    """Core's own `SpendTooMuch`: one satoshi more than `spend_tx` holds."""
    return _create_tx_with_script(
        spend_tx, _spend_avail(spend_tx) + 1, output_script=_BASIC_P2SH
    )


def _create_negative(spend_tx: Tx) -> Tx:
    """Core's own `CreateNegative`."""
    return _create_tx_with_script(spend_tx, -1)


def _create_too_large(spend_tx: Tx) -> Tx:
    """Core's own `CreateTooLarge`."""
    return _create_tx_with_script(spend_tx, _MAX_MONEY + 1)


def _create_sum_too_large(spend_tx: Tx) -> Tx:
    """Core's own `CreateSumTooLarge`: two outputs of `MAX_MONEY` each."""
    tx = _create_tx_with_script(spend_tx, _MAX_MONEY)
    return _tx(tx.vin, [tx.vout[0]] * 2)


def _invalid_op_if_construction(spend_tx: Tx) -> Tx:
    """Core's own `InvalidOPIFConstruction`: `OP_ENDIF`s with no `OP_IF`."""
    return _create_tx_with_script(
        spend_tx, _spend_avail(spend_tx) // 2, script_sig=_OP_ENDIF * 35
    )


def _too_many_sigops_per_block(spend_tx: Tx) -> Tx:
    """Core's own `TooManySigopsPerBlock`."""
    return _create_tx_with_script(
        spend_tx, 1, output_script=_OP_CHECKSIG * _MAX_BLOCK_SIGOPS
    )


def _too_many_sigops_per_transaction(spend_tx: Tx) -> Tx:
    """Core's own `TooManySigopsPerTransaction`."""
    return _create_tx_with_script(
        spend_tx, 1, output_script=_OP_CHECKSIG * (_MAX_STANDARD_TX_SIGOPS + 1)
    )


def _non_standard_and_invalid(spend_tx: Tx) -> Tx:
    """Core's own `NonStandardAndInvalid`: a scriptSig that is not push-only.

    It fails on its `OP_RETURN` too.
    """
    script_sig = b"\x00" * 3 + _OP_CODESEPARATOR + _OP_RETURN
    return _create_tx_with_script(
        spend_tx, _spend_avail(spend_tx) // 2, script_sig=script_sig
    )


def _disabled_op_code(op_code: str) -> BadTxTemplate:
    """Core's own `getDisabledOpcodeTemplate`."""

    def get_tx(spend_tx: Tx) -> Tx:
        vin = _valid_txin(spend_tx, script_serialize([op_code]))
        return _tx([vin], [_out(1, _BASIC_P2SH)])

    return BadTxTemplate(
        name=f"DisabledOpcode_{op_code}",
        get_tx=get_tx,
        reject_reason="disabled opcode",
        std_reject_reason="scriptsig-not-pushonly",
    )


TEMPLATES: tuple[BadTxTemplate, ...] = (
    BadTxTemplate(
        name="OutputMissing",
        get_tx=_output_missing,
        reject_reason="bad-txns-vout-empty",
    ),
    BadTxTemplate(
        name="InputMissing",
        get_tx=_input_missing,
        reject_reason="bad-txns-vin-empty",
    ),
    BadTxTemplate(
        name="SizeTooSmall",
        get_tx=_size_too_small,
        reject_reason="tx-size-small",
        valid_in_block=True,
    ),
    BadTxTemplate(
        name="ExtraWitness",
        get_tx=_extra_witness,
        reject_reason="tx-size-small",
        std_reject_reason="scriptpubkey",
        block_reject_reason=(
            "block-script-verify-flag-failed (Witness provided for non-witness script)"
        ),
    ),
    BadTxTemplate(
        name="BadInputOutpointIndex",
        get_tx=_bad_input_outpoint_index,
        reject_reason=None,
        block_reject_reason="bad-txns-inputs-missingorspent",
    ),
    BadTxTemplate(
        name="DuplicateInput",
        get_tx=_duplicate_input,
        reject_reason="bad-txns-inputs-duplicate",
    ),
    BadTxTemplate(
        name="PrevoutNullInput",
        get_tx=_prevout_null_input,
        reject_reason="bad-txns-prevout-null",
    ),
    BadTxTemplate(
        name="NonexistentInput",
        get_tx=_nonexistent_input,
        reject_reason=None,
        block_reject_reason="bad-txns-inputs-missingorspent",
    ),
    BadTxTemplate(
        name="SpendTooMuch",
        get_tx=_spend_too_much,
        reject_reason="bad-txns-in-belowout",
    ),
    BadTxTemplate(
        name="CreateNegative",
        get_tx=_create_negative,
        reject_reason="bad-txns-vout-negative",
    ),
    BadTxTemplate(
        name="CreateTooLarge",
        get_tx=_create_too_large,
        reject_reason="bad-txns-vout-toolarge",
    ),
    BadTxTemplate(
        name="CreateSumTooLarge",
        get_tx=_create_sum_too_large,
        reject_reason="bad-txns-txouttotal-toolarge",
    ),
    BadTxTemplate(
        name="InvalidOPIFConstruction",
        get_tx=_invalid_op_if_construction,
        reject_reason="mempool-script-verify-flag-failed (Invalid OP_IF construction)",
        std_reject_reason="scriptsig-not-pushonly",
    ),
    BadTxTemplate(
        name="TooManySigopsPerBlock",
        get_tx=_too_many_sigops_per_block,
        reject_reason="bad-txns-too-many-sigops",
        std_reject_reason="scriptpubkey",
        block_reject_reason="bad-blk-sigops, out-of-bounds SigOpCount",
    ),
    BadTxTemplate(
        name="TooManySigopsPerTransaction",
        get_tx=_too_many_sigops_per_transaction,
        reject_reason="bad-txns-too-many-sigops",
        std_reject_reason="scriptpubkey",
        valid_in_block=True,
    ),
    BadTxTemplate(
        name="NonStandardAndInvalid",
        get_tx=_non_standard_and_invalid,
        reject_reason="mempool-script-verify-flag-failed (OP_RETURN was encountered)",
        std_reject_reason="scriptsig-not-pushonly",
        block_reject_reason=(
            "block-script-verify-flag-failed (OP_RETURN was encountered)"
        ),
    ),
    *(_disabled_op_code(op_code) for op_code in _DISABLED_OP_CODES),
)
