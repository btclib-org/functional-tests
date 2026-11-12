# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_csv_activation`, one body over each node.

Read from Core's `test/functional/feature_csv_activation.py`
(`fa6f7e36a8c2`, 2026-10-23), on the option and MiniWallet families together
([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)):
`-testactivationheight=csv@N` (`Capability.TEST_ACTIVATION_HEIGHT`)
holds BIP68, BIP112 and BIP113 -- one deployment, `csv` -- inactive until
a chosen height, and blocks built and submitted here (`Capability.MINE`)
reach it.

Kept: `getdeploymentinfo`'s own `csv` entry, transitioning one block
before the configured height. CSV carries no buried-deployment version
floor of its own -- `src/validation.cpp`'s own
`ContextualCheckBlockHeader` reads only `DEPLOYMENT_HEIGHTINCB`,
`DEPLOYMENT_DERSIG` and `DEPLOYMENT_CLTV` for its version check -- so,
unlike its two siblings, this file has no version-floor refusal to
observe on the wire or in the log.

Core's own body is kept too, step by step and at Core's own heights
([ISS 167](https://github.com/btclib-org/functional-tests/issues/167)).
Coinbases pay `mini_wallet.py`'s `RAW_P2PK_SCRIPT_PUB_KEY`, Core's
`RAW_P2PK` output, and `raw_p2pk_script_sig` signs every spend; a
BIP112 spend then has `OP_CHECKSEQUENCEVERIFY`, and the argument it
checks where there is one, prepended to that scriptSig, as Core's does.
Each spend goes to a block of its own or with others, and the block is
accepted or refused in Core's wording:

- in the block before the configured height every spend is accepted,
  version 1 and version 2 alike, and at that height BIP113 refuses a
  lock time equal to the median time past, which pins where BIP113
  starts; BIP68's and BIP112's refusals follow a few blocks on, as in
  Core's own file;
- BIP68's relative lock times are refused until the input is deep
  enough, by height or by time, and a version-1 spend never is;
- BIP112's `OP_CHECKSEQUENCEVERIFY` refuses a negative argument, an
  empty stack, and an argument the input's own nSequence does not
  satisfy.

Where Core delivers a block over p2p and reads a refusal from the debug
log, this submits it and reads `submitblock`'s answer, which carries the
same reason. Core timestamps its blocks by hand, ten minutes apart and
far enough in the past for the chain to stay behind the node's clock,
and has the node mine under `setmocktime` twice: the coinbases, before
the first of those, and the block confirming the inputs, ten minutes
after the last. Both are built here instead, timestamped in those same
places, so no `Capability.CLOCK` is asked for. After each accepted block
Core's `invalidateblock` (`Capability.INVALIDATE_BLOCK`) takes it back
off, so the next check builds on the same tip.

Each `feature_csv_activation_<node>_test.py` module beside this one runs
these, `tests/integration/conftest.py`'s own module docstring having
how.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from itertools import product
from typing import TYPE_CHECKING

from btclib.script.script import serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxIn, TxOut

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import (
    FEE,
    RAW_P2PK_SCRIPT_PUB_KEY,
    MiniWallet,
    build_next_block,
    raw_p2pk_script_sig,
)
from tests.integration.script_verify_flag_test import block_script_verify_flag_failed

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.block.block import Block

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "csv_activates_one_block_before_the_configured_height",
    "csv_rules_are_enforced_from_the_configured_height",
]

# Core's own file hardcodes 432; the transition needs only that the
# height is reached in a handful of mined blocks
_CSV_HEIGHT = 12

# Core's own height, which its body runs at: the coinbases its inputs
# spend mature below it
_CORE_CSV_HEIGHT = 432

# Core's own constants: how many inputs its test transactions spend,
# each from a coinbase of its own, and the bits of a relative lock time
_TESTING_TX_COUNT = 83
_BASE_RELATIVE_LOCKTIME = 10
_SEQ_DISABLE_FLAG = 1 << 31
_SEQ_RANDOM_HIGH_BIT = 1 << 25
_SEQ_TYPE_FLAG = 1 << 22
_SEQ_RANDOM_LOW_BIT = 1 << 18

# Core's `create_coinbase` default output, a bare `OP_TRUE`
_RAW_OP_TRUE = ScriptPubKey(b"\x51", check_validity=False)

_NONFINAL = "bad-txns-nonfinal"
# the reasons a script failure is refused for, after the build's own
# `*-script-verify-flag-failed` (`_Chain.script_failure`)
_NEGATIVE = " (Negative locktime)"
_STACK_SIZE = " (Operation not valid with the current stack size)"
_UNSATISFIED = " (Locktime requirement not satisfied)"


def _relative_locktime(sdf: bool, srhb: bool, stf: bool, srlb: bool) -> int:
    """Return Core's `relative_locktime`: the base, with the bits named set."""
    locktime = _BASE_RELATIVE_LOCKTIME
    if sdf:
        locktime |= _SEQ_DISABLE_FLAG
    if srhb:
        locktime |= _SEQ_RANDOM_HIGH_BIT
    if stf:
        locktime |= _SEQ_TYPE_FLAG
    if srlb:
        locktime |= _SEQ_RANDOM_LOW_BIT
    return locktime


def _script_int(value: int) -> int | str:
    """Return `value` the way Core's `CScript` writes it, small as an opcode."""
    if value == -1:
        return "OP_1NEGATE"
    return f"OP_{value}" if 1 <= value <= 16 else value


@dataclass
class _Spend:
    """One of Core's test transactions, its coin and two of its bits."""

    tx: Tx
    coin: Tx
    sdf: bool
    stf: bool


def _spend(
    coin: Tx,
    *,
    version: int = 2,
    sequence: int = 0,
    lock_time: int = 0,
    prepend: Sequence[int | str] = (),
) -> Tx:
    """Return a signed spend of `coin`'s first output, `prepend` ahead.

    Core's `create_self_transfer` in `RAW_P2PK` mode, paying the same
    output back less `FEE`, then `sign_tx`; `prepend` is what Core then
    writes ahead of the signature it signed without.
    """
    tx_in = TxIn(OutPoint(coin.id, 0), sequence=sequence, check_validity=False)
    tx_out = TxOut(coin.vout[0].value - FEE, RAW_P2PK_SCRIPT_PUB_KEY)
    tx = Tx(
        version=version,
        lock_time=lock_time,
        vin=[tx_in],
        vout=[tx_out],
        check_validity=False,
    )
    tx.vin[0].script_sig = serialize(list(prepend)) + raw_p2pk_script_sig(tx, 0)
    return tx


def _bip68_spends(inputs: Sequence[Tx], version: int, delta: int = 0) -> list[_Spend]:
    """Return Core's `create_bip68txs`: every combination of the four bits."""
    spends = []
    for coin, bits in zip(inputs, product([True, False], repeat=4), strict=False):
        locktime = _relative_locktime(*bits)
        tx = _spend(coin, version=version, sequence=locktime + delta)
        spends.append(_Spend(tx, coin, sdf=bits[0], stf=bits[2]))
    return spends


def _bip112_spends(
    inputs: Sequence[Tx], *, vary_op_csv: bool, version: int, delta: int = 0
) -> list[_Spend]:
    """Return Core's `create_bip112txs`: CSV and nSequence, one varied.

    With `vary_op_csv` the argument to `OP_CHECKSEQUENCEVERIFY` carries
    the bits and nSequence is the base; without it, the other way round.
    """
    spends = []
    for coin, bits in zip(inputs, product([True, False], repeat=4), strict=False):
        locktime = _relative_locktime(*bits)
        sequence = _BASE_RELATIVE_LOCKTIME if vary_op_csv else locktime
        argument = locktime if vary_op_csv else _BASE_RELATIVE_LOCKTIME
        prepend = [_script_int(argument), "OP_CHECKSEQUENCEVERIFY", "OP_DROP"]
        tx = _spend(coin, version=version, sequence=sequence + delta, prepend=prepend)
        spends.append(_Spend(tx, coin, sdf=bits[0], stf=bits[2]))
    return spends


def _txs(spends: Sequence[_Spend]) -> list[Tx]:
    """Return Core's `all_rlt_txs`: the transactions alone."""
    return [spend.tx for spend in spends]


def _submit(node: NodeAdapter, block: Block) -> object:
    """Return `submitblock`'s own answer for `block`."""
    return node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])


def _csv(node: NodeAdapter) -> dict[str, object]:
    """Return `getdeploymentinfo`'s own `csv` entry."""
    csv: dict[str, object] = node.rpc.call("getdeploymentinfo")["deployments"]["csv"]
    return csv


def _csv_active(node: NodeAdapter) -> bool:
    """Return Core's `softfork_active(node, 'csv')`."""
    return _csv(node)["active"] is True


class _Chain:
    """Core's own `last_block_time`, and the helpers that build on it."""

    def __init__(self, node: NodeAdapter, last_block_time: int) -> None:
        self.node = node
        self.last_block_time = last_block_time

    def create_test_block(self, txs: Sequence[Tx]) -> Block:
        """Return Core's `create_test_block`: `txs`, ten minutes on."""
        return build_next_block(
            self.node, _RAW_OP_TRUE, txs, version=4, time=self.last_block_time + 600
        )

    def generate_blocks(self, number: int) -> None:
        """Send Core's `generate_blocks`, empty blocks ten minutes apart."""
        for _ in range(number):
            self.send_block(self.create_test_block([]))
            self.last_block_time += 600

    def send_block(self, block: Block, reject_reason: str = "") -> None:
        """Submit `block`: it is the tip, or refused and the tip stays.

        Core's `send_blocks`, one block at a time.
        """
        tip = self.node.rpc.call("getbestblockhash")
        answer = _submit(self.node, block)
        if reject_reason:
            assert answer == reject_reason
        else:
            assert answer is None
            tip = block.header.hash.hex()
        assert self.node.rpc.call("getbestblockhash") == tip

    def accept_and_invalidate(self, txs: Sequence[Tx]) -> None:
        """Check a block carrying `txs` is accepted, then take it back off."""
        self.send_block(self.create_test_block(txs))
        best = self.node.rpc.call("getbestblockhash")
        self.node.rpc.call("invalidateblock", [best])

    def script_failure(self, reason: str) -> str:
        """Return the refusal of a block whose script fails for `reason`."""
        return f"{block_script_verify_flag_failed(self.node)}{reason}"

    def refuse_each(self, txs: Sequence[Tx], reject_reason: str) -> None:
        """Check a block carrying any one of `txs` is refused so."""
        for tx in txs:
            self.send_block(self.create_test_block([tx]), reject_reason)


def _start_with_csv_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    height: int,
) -> NodeAdapter:
    """Return one node, restarted with CSV held back until `height`."""
    (node,) = cluster(1)
    require(Capability.TEST_ACTIVATION_HEIGHT, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart([f"-testactivationheight=csv@{height}"])
    return node


def csv_activates_one_block_before_the_configured_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `getdeploymentinfo`'s `csv` entry tracks the configured height.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_csv_height(cluster, skip_counts, _CSV_HEIGHT)
    wallet = MiniWallet(node)
    assert _csv(node) == {"type": "buried", "active": False, "height": _CSV_HEIGHT}

    wallet.generate(_CSV_HEIGHT - 2)
    assert _csv(node)["active"] is False

    wallet.generate(1)  # tip is now one block before the configured height
    assert _csv(node)["active"] is True


def csv_rules_are_enforced_from_the_configured_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check BIP68, BIP112 and BIP113 are enforced from the configured height.

    Core's own `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_csv_height(cluster, skip_counts, _CORE_CSV_HEIGHT)
    require(Capability.INVALIDATE_BLOCK, node.capabilities, skip_counts)

    # the coinbases, timestamped before `long_past_time`, the time the
    # first block of Core's own `generate_blocks` follows
    long_past_time = int(time.time()) - 600 * 1000
    chain = _Chain(node, long_past_time)
    coinbases = []
    for height in range(1, _TESTING_TX_COUNT + 1):
        block_time = long_past_time - 100 + height
        block = build_next_block(node, RAW_P2PK_SCRIPT_PUB_KEY, time=block_time)
        chain.send_block(block)
        coinbases.append(block.transactions[0])

    # advance to five below the configured height
    chain.generate_blocks(_CORE_CSV_HEIGHT - 5 - _TESTING_TX_COUNT)
    assert not _csv_active(node)

    # the inputs, each spending a coinbase, in Core's own order: the last
    # coinbase first
    def send_generic_input_tx() -> Tx:
        tx = _spend(coinbases.pop())
        raw = tx.serialize(include_witness=False, check_validity=False).hex()
        assert node.rpc.call("sendrawtransaction", [raw]) == tx.id.hex()
        return tx

    bip68inputs = [send_generic_input_tx() for _ in range(16)]
    bip112basicinputs = [[send_generic_input_tx() for _ in range(16)] for _ in range(2)]
    bip112diverseinputs = [
        [send_generic_input_tx() for _ in range(16)] for _ in range(2)
    ]
    bip112specialinput = send_generic_input_tx()
    bip112emptystackinput = send_generic_input_tx()
    bip113input = send_generic_input_tx()
    inputs = [
        *bip68inputs,
        *(tx for group in bip112basicinputs for tx in group),
        *(tx for group in bip112diverseinputs for tx in group),
        bip112specialinput,
        bip112emptystackinput,
        bip113input,
    ]
    chain.send_block(chain.create_test_block(inputs))
    assert node.rpc.call("getrawmempool") == []
    chain.last_block_time += 600

    chain.generate_blocks(2)
    assert node.rpc.call("getblockcount") == _CORE_CSV_HEIGHT - 2
    assert not _csv_active(node)

    def bip113tx(version: int, lock_time: int) -> Tx:
        return _spend(
            bip113input, version=version, sequence=0xFFFFFFFE, lock_time=lock_time
        )

    bip68txs_v1 = _bip68_spends(bip68inputs, 1)
    bip68txs_v2 = _bip68_spends(bip68inputs, 2)
    basic, basic_9 = bip112basicinputs
    diverse, diverse_9 = bip112diverseinputs
    vary_nsequence_v1 = _bip112_spends(basic, vary_op_csv=False, version=1)
    vary_nsequence_v2 = _bip112_spends(basic, vary_op_csv=False, version=2)
    vary_nsequence_9_v1 = _bip112_spends(
        basic_9, vary_op_csv=False, version=1, delta=-1
    )
    vary_nsequence_9_v2 = _bip112_spends(
        basic_9, vary_op_csv=False, version=2, delta=-1
    )
    vary_op_csv_v1 = _bip112_spends(diverse, vary_op_csv=True, version=1)
    vary_op_csv_v2 = _bip112_spends(diverse, vary_op_csv=True, version=2)
    vary_op_csv_9_v1 = _bip112_spends(diverse_9, vary_op_csv=True, version=1, delta=-1)
    vary_op_csv_9_v2 = _bip112_spends(diverse_9, vary_op_csv=True, version=2, delta=-1)
    special = ["OP_1NEGATE", "OP_CHECKSEQUENCEVERIFY", "OP_DROP"]
    special_v1 = _spend(bip112specialinput, version=1, prepend=special)
    special_v2 = _spend(bip112specialinput, version=2, prepend=special)
    empty = ["OP_CHECKSEQUENCEVERIFY"]
    emptystack_v1 = _spend(bip112emptystackinput, version=1, prepend=empty)
    emptystack_v2 = _spend(bip112emptystackinput, version=2, prepend=empty)

    # before the configured height every spend is accepted; BIP113's lock
    # time is the median time past, and less than the block's own time
    for version, bip68txs, bip112txs, special_tx, emptystack_tx in (
        (
            1,
            bip68txs_v1,
            (vary_nsequence_v1, vary_op_csv_v1, vary_nsequence_9_v1, vary_op_csv_9_v1),
            special_v1,
            emptystack_v1,
        ),
        (
            2,
            bip68txs_v2,
            (vary_nsequence_v2, vary_op_csv_v2, vary_nsequence_9_v2, vary_op_csv_9_v2),
            special_v2,
            emptystack_v2,
        ),
    ):
        success_txs = [
            bip113tx(version, chain.last_block_time - 600 * 5),
            special_tx,
            emptystack_tx,
            *_txs(bip68txs),
            *(tx for spends in bip112txs for tx in _txs(spends)),
        ]
        chain.accept_and_invalidate(success_txs)

    # one more block, at the height before the configured one, activates
    # the rules for the next
    assert not _csv_active(node)
    chain.generate_blocks(1)
    assert _csv_active(node)

    # BIP113: a lock time equal to the median time past is refused, and
    # one less is accepted, whatever the version
    mtp = chain.last_block_time - 600 * 5
    chain.refuse_each([bip113tx(1, mtp), bip113tx(2, mtp)], _NONFINAL)
    for version in (1, 2):
        chain.accept_and_invalidate([bip113tx(version, mtp - 1)])

    chain.generate_blocks(4)

    # BIP68: version 1 ignores it
    chain.accept_and_invalidate(_txs(bip68txs_v1))

    # version 2: only the disable flag passes, eight blocks deep and eight
    # times ten minutes being less than ten times 512 seconds
    bip68success_txs = [s.tx for s in bip68txs_v2 if s.sdf]
    chain.accept_and_invalidate(bip68success_txs)
    bip68timetxs = [s.tx for s in bip68txs_v2 if not s.sdf and s.stf]
    chain.refuse_each(bip68timetxs, _NONFINAL)
    bip68heighttxs = [s.tx for s in bip68txs_v2 if not s.sdf and not s.stf]
    chain.refuse_each(bip68heighttxs, _NONFINAL)

    # one block on, the time locks pass and the height ones do not
    chain.generate_blocks(1)
    bip68success_txs.extend(bip68timetxs)
    chain.accept_and_invalidate(bip68success_txs)
    chain.refuse_each(bip68heighttxs, _NONFINAL)

    # one more, and every one passes
    chain.generate_blocks(1)
    bip68success_txs.extend(bip68heighttxs)
    chain.accept_and_invalidate(bip68success_txs)

    # BIP112, version 1: a negative argument and an empty stack fail
    chain.refuse_each([special_v1], chain.script_failure(_NEGATIVE))
    chain.refuse_each([emptystack_v1], chain.script_failure(_STACK_SIZE))
    # with the disable flag in the argument the spends pass
    chain.accept_and_invalidate(
        [s.tx for s in vary_op_csv_v1 if s.sdf]
        + [s.tx for s in vary_op_csv_9_v1 if s.sdf]
    )
    # without it they fail, version 1 carrying no relative lock time
    fail_txs = _txs(vary_nsequence_v1) + _txs(vary_nsequence_9_v1)
    fail_txs += [s.tx for s in vary_op_csv_v1 if not s.sdf]
    fail_txs += [s.tx for s in vary_op_csv_9_v1 if not s.sdf]
    chain.refuse_each(fail_txs, chain.script_failure(_UNSATISFIED))

    # version 2: a negative argument and an empty stack fail
    chain.refuse_each([special_v2], chain.script_failure(_NEGATIVE))
    chain.refuse_each([emptystack_v2], chain.script_failure(_STACK_SIZE))
    # with the disable flag in the argument every sequence lock is met
    chain.accept_and_invalidate(
        [s.tx for s in vary_op_csv_v2 if s.sdf]
        + [s.tx for s in vary_op_csv_9_v2 if s.sdf]
    )
    # nSequence 9 fails, by mismatch or by the check itself
    fail_txs = _txs(vary_nsequence_9_v2)
    fail_txs += [s.tx for s in vary_op_csv_9_v2 if not s.sdf]
    chain.refuse_each(fail_txs, chain.script_failure(_UNSATISFIED))
    # the disable flag in nSequence fails
    chain.refuse_each(
        [s.tx for s in vary_nsequence_v2 if s.sdf], chain.script_failure(_UNSATISFIED)
    )
    # a type mismatch fails
    fail_txs = [s.tx for s in vary_nsequence_v2 if not s.sdf and s.stf]
    fail_txs += [s.tx for s in vary_op_csv_v2 if not s.sdf and s.stf]
    chain.refuse_each(fail_txs, chain.script_failure(_UNSATISFIED))
    # the rest pass, the masking of the other bits working
    chain.accept_and_invalidate(
        [s.tx for s in vary_nsequence_v2 if not s.sdf and not s.stf]
        + [s.tx for s in vary_op_csv_v2 if not s.sdf and not s.stf]
    )

    # two time types compared: nSequence set to a time lock and signed
    # again, Core's `sign_tx` replacing the scriptSig and its prepend
    time_txs = [
        _spend(s.coin, sequence=_BASE_RELATIVE_LOCKTIME | _SEQ_TYPE_FLAG)
        for s in vary_op_csv_v2
        if not s.sdf and s.stf
    ]
    chain.accept_and_invalidate(time_txs)
