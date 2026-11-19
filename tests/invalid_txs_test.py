# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`btclib_functional_tests.invalid_txs`, built without a node.

What a node makes of a block of each template is the subject of
`tests/integration/feature_block_test.py`. This holds the templates to
what Core's own templates build, recorded in `tests/_data/invalid_txs.json`
(`tests/_data/README.md` says how), to what Core's own file asserts of
them, and to the bounds Core's validation refuses them at.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from btclib.block.limits import MAX_BLOCK_SIGOPS_COST, WITNESS_SCALE_FACTOR
from btclib.policy import MAX_STANDARD_TX_SIGOPS_COST, MIN_STANDARD_TX_NONWITNESS_SIZE
from btclib.script.script import BYTE_FROM_OP_CODE_NAME
from btclib.script.sig_ops import sig_op_count
from btclib.tx import Tx

from btclib_functional_tests.invalid_txs import TEMPLATES, BadTxTemplate

_RECORDED: dict[str, Any] = json.loads(
    (Path(__file__).parent / "_data" / "invalid_txs.json").read_text(encoding="utf-8")
)

# one input and two outputs, of 50 and 7 satoshi
_SPEND_TX = Tx.parse(bytes.fromhex(_RECORDED["spend_tx"]), check_validity=False)


def _template(name: str) -> BadTxTemplate:
    return next(template for template in TEMPLATES if template.name == name)


def test_the_templates_are_core_s_in_core_s_order() -> None:
    """The names, in the order Core's `iter_all_templates` gives them."""
    recorded = [entry["name"] for entry in _RECORDED["templates"]]
    assert [template.name for template in TEMPLATES] == recorded


@pytest.mark.parametrize(
    "entry", _RECORDED["templates"], ids=lambda entry: entry["name"]
)
def test_each_template_builds_what_core_s_builds(entry: dict[str, Any]) -> None:
    """The four fields, and the transaction with its witness, byte for byte."""
    template = _template(entry["name"])
    assert template.reject_reason == entry["reject_reason"]
    assert template.std_reject_reason == entry["std_reject_reason"]
    assert template.block_reject_reason == entry["block_reject_reason"]
    assert template.valid_in_block == entry["valid_in_block"]
    tx = template.get_tx(_SPEND_TX)
    assert tx.serialize(include_witness=True, check_validity=False).hex() == entry["tx"]


def test_a_template_a_block_may_not_hold_names_a_reason() -> None:
    """Core's own loop asserts it before it builds the block."""
    for template in TEMPLATES:
        if not template.valid_in_block:
            assert template.block_reject_reason or template.reject_reason


def _output_sig_ops(name: str) -> int:
    """Return the legacy sigops of a template's one output."""
    tx = _template(name).get_tx(_SPEND_TX)
    return sig_op_count(tx.vout[0].script_pub_key.script)


def test_too_many_sigops_per_transaction_is_one_past_the_standard_bound() -> None:
    """Core's mempool refuses a cost past `MAX_STANDARD_TX_SIGOPS_COST`.

    One sigop fewer would be at the bound, and taken.
    """
    cost = _output_sig_ops("TooManySigopsPerTransaction") * WITNESS_SCALE_FACTOR
    assert cost - WITNESS_SCALE_FACTOR == MAX_STANDARD_TX_SIGOPS_COST
    assert _template("TooManySigopsPerTransaction").valid_in_block


def test_too_many_sigops_per_block_is_at_the_block_bound_alone() -> None:
    """Core's `CheckBlock` refuses a block past `MAX_BLOCK_SIGOPS_COST`.

    The template is at the bound alone, so any sigop of the coinbase takes
    the block holding it past.
    """
    cost = _output_sig_ops("TooManySigopsPerBlock") * WITNESS_SCALE_FACTOR
    assert cost == MAX_BLOCK_SIGOPS_COST


def test_size_too_small_is_64_octets_without_its_witness() -> None:
    """Core's own `SizeTooSmall` asserts both."""
    tx = _template("SizeTooSmall").get_tx(_SPEND_TX)
    size = len(tx.serialize(include_witness=False, check_validity=False))
    assert size == 64
    assert size == MIN_STANDARD_TX_NONWITNESS_SIZE - 1


def test_spend_too_much_spends_a_satoshi_more_than_every_output() -> None:
    """Core's `spend_avail` sums every output, not output 0 alone."""
    tx = _template("SpendTooMuch").get_tx(_SPEND_TX)
    assert tx.vout[0].value == 50 + 7 + 1


def test_nonexistent_input_names_the_next_id() -> None:
    """Core's `txid_int + 1`, read in `getrawtransaction`'s byte order."""
    tx = _template("NonexistentInput").get_tx(_SPEND_TX)
    missing = int.from_bytes(tx.vin[0].prev_out.tx_id, "big")
    assert missing == int.from_bytes(_SPEND_TX.id, "big") + 1


def test_extra_witness_carries_a_witness() -> None:
    """The one template whose transaction holds a witness."""
    segwit = [t.name for t in TEMPLATES if t.get_tx(_SPEND_TX).is_segwit]
    assert segwit == ["ExtraWitness"]


def test_a_disabled_op_code_is_the_whole_script_sig() -> None:
    """Core's own `getDisabledOpcodeTemplate`: the op code, then nothing."""
    disabled = [t for t in TEMPLATES if t.name.startswith("DisabledOpcode_")]
    assert disabled
    for template in disabled:
        op_code = template.name.removeprefix("DisabledOpcode_")
        tx = template.get_tx(_SPEND_TX)
        assert tx.vin[0].script_sig == BYTE_FROM_OP_CODE_NAME[op_code]
