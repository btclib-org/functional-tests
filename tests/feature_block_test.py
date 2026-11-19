# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`btclib_functional_tests.feature_block`, built without a node.

What a node makes of the sequence is the subject of
`tests/integration/feature_block_test.py`. This holds the sequence to
what a runner relies on before any node reads it.
"""

from __future__ import annotations

import pytest
from btclib.block.genesis import genesis_block
from btclib.script.script import BYTE_FROM_OP_CODE_NAME

from btclib_functional_tests.feature_block import (
    BlockStep,
    InvalidateStep,
    MockTimeStep,
    Step,
    TipStep,
    _check,
    steps,
)
from btclib_functional_tests.invalid_txs import TEMPLATES

_START = 1_800_000_000


@pytest.fixture(scope="module")
def sequence() -> list[Step]:
    """Return the sequence once for the module: building it signs thousands."""
    return steps(_START)


def _block_steps(sequence: list[Step]) -> list[BlockStep]:
    return [step for step in sequence if isinstance(step, BlockStep)]


def test_the_first_block_is_one_second_past_the_start(sequence: list[Step]) -> None:
    """Core's first block, on genesis, at `time.time() + 1`."""
    first = _block_steps(sequence)[0]
    genesis_hash = genesis_block("regtest").header.hash
    assert first.block.header.previous_block_hash == genesis_hash
    assert int(first.block.header.time.timestamp()) == _START + 1


def test_a_refused_block_never_becomes_the_tip(sequence: list[Step]) -> None:
    """A step naming a reject reason expects the tip elsewhere."""
    refused = [step for step in _block_steps(sequence) if step.reject_reason]
    assert refused
    assert not any(step.becomes_tip for step in refused)


def test_every_block_builds_on_one_sent_before_it(sequence: list[Step]) -> None:
    """No block's parent is unknown to the node when the block arrives."""
    known = {genesis_block("regtest").header.hash}
    for step in _block_steps(sequence):
        assert step.block.header.previous_block_hash in known, step.name
        known.add(step.block.header.hash)


def test_every_named_hash_is_a_block_sent_before_it(sequence: list[Step]) -> None:
    """A tip or an invalidation names a block the node has been sent."""
    sent: set[bytes] = set()
    for step in sequence:
        if isinstance(step, BlockStep):
            sent.add(step.block.header.hash)
        elif isinstance(step, TipStep | InvalidateStep):
            assert step.block_hash in sent, step.name


def test_the_clock_is_set_and_given_back(sequence: list[Step]) -> None:
    """The clock moves past the start, and the node gets its own back."""
    clock = [step.timestamp for step in sequence if isinstance(step, MockTimeStep)]
    assert clock[0] > _START
    assert clock[-1] == 0


def test_the_same_start_builds_the_same_sequence(sequence: list[Step]) -> None:
    """The fixed key and RFC 6979 leave nothing to chance."""
    assert steps(_START) == sequence


def test_two_names_share_a_hash_only_where_core_builds_them_to(
    sequence: list[Step],
) -> None:
    """Each pair is a block and its CVE-2012-2459 or bloated copy.

    Any other pair is two blocks Core's random signatures would tell
    apart and this sequence does not, `b2` and `b3` among them.
    """
    names: dict[bytes, set[str]] = {}
    for step in _block_steps(sequence):
        names.setdefault(step.block.header.hash, set()).add(step.name)
    shared = {frozenset(n) for n in names.values() if len(n) > 1}
    assert shared == {
        frozenset({"b56", "b57"}),
        frozenset({"b56p2", "b57p2"}),
        frozenset({"b64a", "b64"}),
        frozenset({"b71", "b72"}),
    }


def test_a_failed_check_names_what_failed() -> None:
    """The sequence's own checks raise, naming what failed."""
    with pytest.raises(AssertionError, match="b23 is not of MAX_BLOCK_WEIGHT"):
        _check(False, "b23 is not of MAX_BLOCK_WEIGHT")


def test_b24_pads_with_the_transaction_core_built_for_b23(
    sequence: list[Step],
) -> None:
    """Core reuses `b23`'s padding, spending `b23`'s second transaction.

    `b24`'s own chain does not hold that output; `bad-blk-length` refuses
    the block before any input is looked up.
    """
    blocks = {step.name: step.block for step in _block_steps(sequence)}
    padding = blocks["b24"].transactions[-1]
    assert padding.vin[0].prev_out.tx_id == blocks["b23"].transactions[1].id


def test_each_template_a_block_may_not_hold_is_refused_on_b2(
    sequence: list[Step],
) -> None:
    """Core's templates loop: a block on `b2` for each, its peer dropped."""
    blocks = _block_steps(sequence)
    b2 = next(step for step in blocks if step.name == "b2")
    refused = [step for step in blocks if step.name.startswith("for_invalid.")]
    assert [step.name for step in refused] == [
        f"for_invalid.{template.name}"
        for template in TEMPLATES
        if not template.valid_in_block
    ]
    for step in refused:
        assert step.block.header.previous_block_hash == b2.block.header.hash
        assert step.reject_reason
        assert step.disconnects


def test_a_failed_script_is_refused_in_the_block_s_words(
    sequence: list[Step],
) -> None:
    """Core's own loop turns a template's mempool wording into the block's."""
    reasons = {step.name: step.reject_reason for step in _block_steps(sequence)}
    assert reasons["for_invalid.InvalidOPIFConstruction"] == (
        "block-script-verify-flag-failed (Invalid OP_IF construction)"
    )
    assert not any(
        reason.startswith("mempool-") for reason in reasons.values() if reason
    )


def test_the_signature_goes_before_the_template_s_script_sig(
    sequence: list[Step],
) -> None:
    """Core's `sign_input_legacy` prepends the signature, keeping the rest."""
    blocks = {step.name: step.block for step in _block_steps(sequence)}
    tx = blocks["for_invalid.InvalidOPIFConstruction"].transactions[1]
    script_sig = tx.vin[0].script_sig
    endifs = BYTE_FROM_OP_CODE_NAME["OP_ENDIF"] * 35
    assert script_sig.endswith(endifs)
    assert len(script_sig) > len(endifs)


def test_a_block_holding_a_witness_commits_to_it(sequence: list[Step]) -> None:
    """Core's own `update_block` adds the commitment its witness asks for."""
    blocks = {step.name: step.block for step in _block_steps(sequence)}
    block = blocks["for_invalid.ExtraWitness"]
    assert block.transactions[1].is_segwit
    assert block.witness_commitment is not None
    block.assert_valid_witness_commitment()
    assert not any(
        step.block.is_segwit
        for step in _block_steps(sequence)
        if step.name != "for_invalid.ExtraWitness"
    )
