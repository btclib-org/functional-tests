# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_block` block sequence, built with btclib as data.

Read from Core's `test/functional/feature_block.py` (`f82043af507a`,
2026-06-30). `steps` returns the sequence as a list of `Step`: each block
with the verdict expected of it, and between the blocks the other things
Core's run does to the node or asks of it. Nothing here reaches a node,
so a runner can feed the list to a node over p2p, as
`tests/integration/feature_block_test.py` does, or to a chain in process.

A runner starts its node on regtest, with BIP34 enforced from
`BIP34_HEIGHT`: Core's own `-testactivationheight=bip34@2`. The first
block's coinbase is valid at height 1 only because BIP34 is not yet
enforced there, and a block of the same coinbase is the BIP30 check
further on.

Each block is built as Core's `next_block` and `update_block` build it:
a coinbase paying the subsidy to one key and, where the block spends a
saved coinbase, a transaction paying one satoshi of it on and leaving
the rest to the block's coinbase as fees. Core draws that key and each
signature's nonce at random on each run. Here the key is fixed and each
nonce is RFC 6979's (`_Chain.signature_push`), so `steps` returns the
same blocks for the same `start_time`.

What differs from Core's file:

- the invalid-transaction templates of `test/functional/data/invalid_txs.py`
  are not in the sequence, and neither is the re-org of
  `LARGE_REORG_SIZE` blocks, which Core's own `--skipreorg` leaves out
  ([ISS 439](https://github.com/btclib-org/functional-tests/issues/439));
- Core mines a block spending the output of the largest valid script
  with `generateblock`, and asks `generateblock` for one spending the
  output of the smallest invalid one. Here both are blocks built like
  every other: the first taken and then invalidated, the second refused;
- each block is its own step, where Core sends some in one batch and
  checks the tip once after the last. The step says whether each block
  of a batch becomes the tip.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from btclib.block.block import Block, merkle_root_and_mutated_from_transactions
from btclib.block.block_header import BlockHeader
from btclib.block.genesis import genesis_block
from btclib.block.limits import (
    MAX_BLOCK_SIGOPS_COST,
    MAX_BLOCK_WEIGHT,
    MAX_FUTURE_BLOCK_TIME,
    WITNESS_SCALE_FACTOR,
)
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import (
    CONSENSUS_PARAMS,
    MAX_SCRIPT_ELEMENT_SIZE,
    MAX_SCRIPT_SIZE,
    subsidy,
)
from btclib.key import PrvKeyData
from btclib.script import sig_hash
from btclib.script.script import BYTE_FROM_OP_CODE_NAME
from btclib.script.script import serialize as script_serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.script.sig_ops import sig_op_count
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import SEQUENCE_FINAL
from ecc.dsa import sign_
from ecc.rfc6979_nonce import rfc6979_nonce_

__all__ = [
    "BIP34_HEIGHT",
    "BlockStep",
    "CoinStep",
    "InvalidateStep",
    "MempoolStep",
    "MockTimeStep",
    "Step",
    "TipStep",
    "steps",
]

# the height from which the sequence's chain enforces BIP34, Core's own
# `-testactivationheight=bip34@2`
BIP34_HEIGHT = 2


@dataclass(frozen=True, kw_only=True)
class BlockStep:
    """A block sent to the node, and what the node is expected to make of it.

    :param name: Core's own name for the block, `b23` or `dup_cb`.
    :param block: the block.
    :param becomes_tip: whether the block is the node's best block once
        the node has processed it. Core's `success`.
    :param reject_reason: the reason the node refuses the block with, in
        Core's words; `None` where the node does not refuse it.
    :param unrequested: the block is sent without its header first, the
        node having no reason to ask for it. Core's `force_send`.
    :param disconnects: the node drops the peer that sent the block.
        Core's `reconnect`.
    :param serialized: what is sent in place of the block's own
        serialization, where that is the subject of the step.
    """

    name: str
    block: Block
    becomes_tip: bool
    reject_reason: str | None = None
    unrequested: bool = False
    disconnects: bool = False
    serialized: bytes | None = None


@dataclass(frozen=True, kw_only=True)
class TipStep:
    """The node's best block is the one named.

    :param name: Core's own name for the block.
    :param block_hash: its hash, in the byte order `getbestblockhash`
        prints.
    """

    name: str
    block_hash: bytes


@dataclass(frozen=True, kw_only=True)
class InvalidateStep:
    """The node marks a block invalid and goes back to the best valid chain.

    Core's `invalidateblock`.

    :param name: Core's own name for the block.
    :param block_hash: its hash, in the byte order `getbestblockhash`
        prints.
    """

    name: str
    block_hash: bytes


@dataclass(frozen=True, kw_only=True)
class MockTimeStep:
    """The node's clock reads `timestamp` from now on.

    Core's `setmocktime`: `0` gives the node its own clock back.
    """

    timestamp: int


@dataclass(frozen=True, kw_only=True)
class MempoolStep:
    """The node's mempool holds these transactions and no other.

    :param tx_ids: each transaction's id, in the byte order
        `getrawmempool` prints.
    """

    tx_ids: frozenset[bytes]


@dataclass(frozen=True, kw_only=True)
class CoinStep:
    """An output is unspent, with this many confirmations.

    Core's `gettxout`.

    :param tx_id: the id of the transaction creating the output.
    :param vout: the output's index in that transaction.
    :param confirmations: the depth of the block holding that
        transaction, the tip counting one.
    """

    tx_id: bytes
    vout: int
    confirmations: int


type Step = BlockStep | TipStep | InvalidateStep | MockTimeStep | MempoolStep | CoinStep

# Core's own `COIN` (`test_framework/messages.py`)
_COIN = 100_000_000

# Core's own `MAX_BLOCK_SIGOPS` (`test_framework/blocktools.py`): the
# bound in legacy sigops, each costing `WITNESS_SCALE_FACTOR`
_MAX_BLOCK_SIGOPS = MAX_BLOCK_SIGOPS_COST // WITNESS_SCALE_FACTOR

# Core's own `create_block` default, `VERSIONBITS_LAST_OLD_BLOCK_VERSION`
_VERSION = 4

# Core's own `CTransaction` default
_TX_VERSION = 2

# Core's own `MAX_SEQUENCE_NONFINAL`, the coinbase input's sequence
_MAX_SEQUENCE_NONFINAL = 0xFFFFFFFE

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

_GENESIS_HASH = genesis_block("regtest").header.hash

# the key every coinbase pays, and every signature is made with
_KEY = PrvKeyData(2, "regtest")
_COINBASE_SCRIPT = ScriptPubKey.p2pk(_KEY.pub).script

_OP_TRUE = script_serialize(["OP_TRUE"])
_OP_RETURN = script_serialize(["OP_RETURN"])
_OP_CHECKSIG = BYTE_FROM_OP_CODE_NAME["OP_CHECKSIG"]
_OP_CHECKSIGVERIFY = BYTE_FROM_OP_CODE_NAME["OP_CHECKSIGVERIFY"]
_OP_CHECKMULTISIG = BYTE_FROM_OP_CODE_NAME["OP_CHECKMULTISIG"]
_OP_CHECKMULTISIGVERIFY = BYTE_FROM_OP_CODE_NAME["OP_CHECKMULTISIGVERIFY"]
_OP_PUSHDATA4 = BYTE_FROM_OP_CODE_NAME["OP_PUSHDATA4"][0]

# Core's own `OP_INVALIDOPCODE`, a byte btclib names no op code for
_OP_INVALIDOPCODE = b"\xff"

# Core's own `create_tx` default output script
_DROPS = script_serialize(["OP_TRUE", "OP_DROP"] * 15 + ["OP_TRUE"])

# Core's own `DUPLICATE_COINBASE_SCRIPT_SIG`: a push of 120, so a coinbase
# carrying it at height 1, before BIP34 is enforced, is the same
# transaction as one carrying it at height 120
_DUPLICATE_COINBASE_SCRIPT_SIG = b"\x01\x78"
_DUPLICATE_COINBASE_HEIGHT = 120

# what Core's arithmetic on a maximum-weight block leaves for the
# transaction carrying the padding: every octet of it but the zeros it
# pushes, in weight
_PADDING_OVERHEAD = 276

# Core's own `NUM_BUFFER_BLOCKS_TO_GENERATE` and `NUM_OUTPUTS_TO_COLLECT`
_BUFFER_BLOCKS = 99
_OUTPUTS_TO_COLLECT = 33

# Core's own bloated block: the transaction count as `0xff` and eight
# octets, where one octet is the canonical encoding
_BLOATED_COUNT_PREFIX = b"\xff"

# the transaction b70 spends, which does not exist: Core's own
# `uint256_from_str` of a hex string, which reads the string's first
# thirty-two ASCII octets as the id's wire bytes
_BOGUS_TX_ID = b"23c70ed7c0506e9178fc1a987f40a33946d4ad4c962b5ae3a52546da53af0c5c"[:32][
    ::-1
]


def _check(condition: bool, what: str) -> None:
    """Raise where the sequence is not what Core's own checks say it is.

    :raises AssertionError: naming `what`.
    """
    if not condition:
        raise AssertionError(what)


def _time_of(block: Block) -> int:
    """Return a block's own timestamp."""
    return int(block.header.time.timestamp())


def _solve(header: BlockHeader) -> BlockHeader:
    """Return `header` solved from nonce zero, as Core's `solve` would."""
    candidate = BlockHeader(
        header.version,
        header.previous_block_hash,
        header.merkle_root,
        header.time,
        header.bits,
        0,
        check_validity=False,
    )
    solved = mine(candidate)
    _check(solved is not None, "no nonce solves the header")
    return cast("BlockHeader", solved)


def _make_block(
    previous: bytes,
    transactions: list[Tx],
    time: int,
    *,
    version: int = _VERSION,
    bits: bytes = REGTEST_POW_LIMIT_BITS,
) -> Block:
    """Return the solved block over `transactions`, built without checks.

    `transactions` may be empty, hold no coinbase, or repeat a
    transaction: Core's `create_block` and `update_block` build such
    blocks to have them refused.
    """
    merkle_root, _ = merkle_root_and_mutated_from_transactions(transactions)
    header = BlockHeader(
        version,
        previous,
        merkle_root,
        datetime.fromtimestamp(time, UTC),
        bits,
        0,
        check_validity=False,
    )
    return Block(_solve(header), transactions, check_validity=False)


def _with_header(
    block: Block, *, merkle_root: bytes | None = None, bits: bytes | None = None
) -> Block:
    """Return `block` with its merkle root or bits changed, solved again."""
    header = BlockHeader(
        block.header.version,
        block.header.previous_block_hash,
        block.header.merkle_root if merkle_root is None else merkle_root,
        block.header.time,
        block.header.bits if bits is None else bits,
        0,
        check_validity=False,
    )
    return Block(_solve(header), block.transactions, check_validity=False)


def _coinbase_script_sig(height: int) -> bytes:
    """Core's own `script_BIP34_coinbase_height`, padded below seventeen."""
    if height <= 16:
        return script_serialize([f"OP_{height}", "OP_0"])
    return script_serialize([height])


def _coinbase(
    height: int,
    *,
    value: int | None = None,
    lock_time: int | None = None,
    script_sig: bytes | None = None,
    sequence: int = _MAX_SEQUENCE_NONFINAL,
    extra_outputs: tuple[TxOut, ...] = (),
) -> Tx:
    """Core's own `create_coinbase(height, pubkey)`, each field overridable."""
    return Tx(
        _TX_VERSION,
        height - 1 if lock_time is None else lock_time,
        [
            TxIn(
                OutPoint(),
                _coinbase_script_sig(height) if script_sig is None else script_sig,
                sequence,
                check_validity=False,
            )
        ],
        [
            TxOut(
                subsidy(height, _HALVING_INTERVAL) if value is None else value,
                _COINBASE_SCRIPT,
            ),
            *extra_outputs,
        ],
        check_validity=False,
    )


def _create_tx(spend: Tx, n: int, value: int, script: bytes = _DROPS) -> Tx:
    """Core's own `create_tx`: output `n` of `spend` paying `value`."""
    _check(n < len(spend.vout), "no such output to spend")
    return Tx(
        _TX_VERSION,
        0,
        [TxIn(OutPoint(spend.id, n), b"", SEQUENCE_FINAL, check_validity=False)],
        [TxOut(value, script, check_validity=False)],
        check_validity=False,
    )


def _with_script_sig(tx: Tx, vin_i: int, script_sig: bytes) -> Tx:
    """Return `tx` with one input's scriptSig replaced."""
    vin = list(tx.vin)
    old = vin[vin_i]
    vin[vin_i] = TxIn(old.prev_out, script_sig, old.sequence, check_validity=False)
    return Tx(tx.version, tx.lock_time, vin, tx.vout, check_validity=False)


def _with_outputs(tx: Tx, *outputs: TxOut) -> Tx:
    """Return `tx` with `outputs` appended."""
    return Tx(
        tx.version, tx.lock_time, tx.vin, [*tx.vout, *outputs], check_validity=False
    )


def _legacy_sig_op_count(block: Block) -> int:
    """Core's own `get_legacy_sigopcount_block`, counted accurately."""
    return sum(
        sig_op_count(tx_out.script_pub_key.script, accurate=True)
        for tx in block.transactions
        for tx_out in tx.vout
    ) + sum(
        sig_op_count(tx_in.script_sig, accurate=True)
        for tx in block.transactions
        for tx_in in tx.vin
    )


def _padding_tx(block: Block, extra: int = 0, *, spend: Tx | None = None) -> Tx:
    """Return Core's transaction filling `block` to `MAX_BLOCK_WEIGHT`.

    It spends output 0 of `spend`, the block's second transaction where
    not given, and pushes zeros: `extra` more of them push the block past
    the bound.
    """
    if spend is None:
        spend = block.transactions[1]
    script_length = (MAX_BLOCK_WEIGHT - block.weight - _PADDING_OVERHEAD) // 4
    return Tx(
        _TX_VERSION,
        0,
        [TxIn(OutPoint(spend.id, 0), b"", 0, check_validity=False)],
        [
            TxOut(
                0,
                script_serialize([b"\x00" * (script_length + extra)]),
                check_validity=False,
            )
        ],
        check_validity=False,
    )


class _Chain:
    """Core's own bookkeeping in `FullBlockTest`, and the steps it emits.

    `blocks` names every block built, `heights` gives each hash its
    height, `tip` is the block the next one is built on, and
    `spendable` holds the blocks whose coinbase a later block spends.
    """

    def __init__(self, start_time: int) -> None:
        self.start_time = start_time
        self.blocks: dict[str, Block] = {}
        self.heights: dict[bytes, int] = {_GENESIS_HASH: 0}
        self.tip: Block | None = None
        self.spendable: list[Block] = []
        self.steps: list[Step] = []
        self.signing_for = ""

    def signature_push(self, tx: Tx, vin_i: int, script: bytes) -> bytes:
        """Return the push of `_KEY`'s `SIGHASH_ALL` signature of one input.

        Core's own `sign_input_legacy` prepends this to the input's
        scriptSig. Core signs with a random nonce, which is what tells
        apart two blocks it builds alike, `b2` and `b3` among them; the
        nonce here is RFC 6979's, given the name of the block being built
        as its extra data.
        """
        digest = sig_hash.legacy(script, tx, vin_i, sig_hash.ALL)
        nonce = rfc6979_nonce_(digest, _KEY.q, extra_entropy=self.signing_for.encode())
        signature = sign_(digest, _KEY.q, nonce, grind=False).serialize()
        return script_serialize([signature + bytes([sig_hash.ALL])])

    def sign_tx(self, tx: Tx, spend: Tx) -> Tx:
        """Core's own `sign_tx`: sign input 0, spending output 0 of `spend`.

        An `OP_TRUE` output needs no signature, and its scriptSig is left
        empty.
        """
        script = spend.vout[0].script_pub_key.script
        if script[:1] == _OP_TRUE:
            return _with_script_sig(tx, 0, b"")
        return _with_script_sig(tx, 0, self.signature_push(tx, 0, script))

    def create_and_sign(self, spend: Tx, value: int, script: bytes = _OP_TRUE) -> Tx:
        """Core's own `create_and_sign_transaction`, spending output 0."""
        return self.sign_tx(_create_tx(spend, 0, value, script), spend)

    def height(self, name: str) -> int:
        """Return the height of a named block."""
        return self.heights[self.blocks[name].header.hash]

    def _register(self, name: str, block: Block, height: int) -> Block:
        """Make `block` the tip, under `name`."""
        self.tip = block
        self.heights[block.header.hash] = height
        self.blocks[name] = block
        return block

    def next_block(
        self,
        name: str,
        spend: Tx | None = None,
        additional_coinbase_value: int = 0,
        *,
        script: bytes = _OP_TRUE,
        version: int = _VERSION,
        additional_output_scripts: tuple[bytes, ...] = (),
    ) -> Block:
        """Core's own `next_block`: a block on `tip`, spending `spend`."""
        _check(name not in self.blocks, f"{name} built twice")
        self.signing_for = name
        if self.tip is None:
            previous = _GENESIS_HASH
            block_time = self.start_time + 1
        else:
            previous = self.tip.header.hash
            block_time = _time_of(self.tip) + 1
        height = self.heights[previous] + 1
        value = subsidy(height, _HALVING_INTERVAL) + additional_coinbase_value
        transactions: list[Tx] = []
        if spend is not None:
            value += spend.vout[0].value - 1
            transactions.append(self.sign_tx(_create_tx(spend, 0, 1, script), spend))
        coinbase = _coinbase(
            height,
            value=value,
            extra_outputs=tuple(
                TxOut(0, s, check_validity=False) for s in additional_output_scripts
            ),
        )
        block = _make_block(
            previous, [coinbase, *transactions], block_time, version=version
        )
        return self._register(name, block, height)

    def rebuild(
        self,
        name: str,
        transactions: list[Tx],
        *,
        time: int | None = None,
    ) -> Block:
        """Core's own `update_block`, given the whole transaction list."""
        old = self.blocks[name]
        height = self.heights.pop(old.header.hash)
        block = _make_block(
            old.header.previous_block_hash,
            transactions,
            _time_of(old) if time is None else time,
            version=old.header.version,
            bits=old.header.bits,
        )
        return self._register(name, block, height)

    def update_block(
        self, name: str, new_transactions: list[Tx], *, time: int | None = None
    ) -> Block:
        """Core's own `update_block`: `new_transactions` appended."""
        old = self.blocks[name]
        return self.rebuild(name, [*old.transactions, *new_transactions], time=time)

    def update_coinbase(self, name: str, coinbase: Tx) -> Block:
        """Replace a block's coinbase, as Core does before `update_block`."""
        old = self.blocks[name]
        return self.rebuild(name, [coinbase, *old.transactions[1:]])

    def move_tip(self, name: str) -> None:
        """Core's own `move_tip`."""
        self.tip = self.blocks[name]

    def save_spendable_output(self) -> None:
        """Core's own `save_spendable_output`."""
        _check(self.tip is not None, "no block to save")
        self.spendable.append(cast("Block", self.tip))

    def get_spendable_output(self) -> Tx:
        """Core's own `get_spendable_output`: the oldest saved coinbase."""
        return self.spendable.pop(0).transactions[0]

    def send(
        self,
        name: str,
        *,
        becomes_tip: bool = True,
        reject_reason: str | None = None,
        unrequested: bool = False,
        disconnects: bool = False,
        block: Block | None = None,
        serialized: bytes | None = None,
    ) -> None:
        """Core's own `send_blocks([block], ...)`, as a step.

        :param block: the block sent, where not the one named.
        """
        self.steps.append(
            BlockStep(
                name=name,
                block=self.blocks[name] if block is None else block,
                becomes_tip=becomes_tip,
                reject_reason=reject_reason,
                unrequested=unrequested,
                disconnects=disconnects,
                serialized=serialized,
            )
        )

    def reject(
        self,
        name: str,
        reject_reason: str,
        *,
        unrequested: bool = False,
        disconnects: bool = True,
        block: Block | None = None,
    ) -> None:
        """Core's own `send_blocks([block], success=False, reject_reason=r)`.

        The node drops the peer, unless `disconnects` says otherwise.
        """
        self.send(
            name,
            becomes_tip=False,
            reject_reason=reject_reason,
            unrequested=unrequested,
            disconnects=disconnects,
            block=block,
        )


def _fork_and_reorg(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b1` to `b14`: forks, re-orgs, the coinbase's value."""
    #     genesis -> b1 (0) -> b2 (1)
    chain.next_block("b1", spend=out[0])
    chain.save_spendable_output()
    chain.next_block("b2", spend=out[1])
    chain.save_spendable_output()
    chain.send("b1")
    chain.send("b2")

    # a chain of the same length is not a re-org: b2 was seen first
    #                      \-> b3 (1)
    chain.move_tip("b1")
    chain.next_block("b3", spend=out[1])
    chain.send("b3", becomes_tip=False)

    # a longer chain is
    #                      \-> b3 (1) -> b4 (2)
    chain.next_block("b4", spend=out[2])
    chain.send("b4")

    # and back to the first chain
    #     genesis -> b1 (0) -> b2 (1) -> b5 (2) -> b6 (3)
    chain.move_tip("b2")
    chain.next_block("b5", spend=out[2])
    chain.save_spendable_output()
    chain.send("b5", becomes_tip=False)
    chain.next_block("b6", spend=out[3])
    chain.send("b6")

    # a longer chain spending an output twice is not taken
    #                                          \-> b7 (2) -> b8 (4)
    chain.move_tip("b5")
    chain.next_block("b7", spend=out[2])
    chain.send("b7", becomes_tip=False)
    chain.next_block("b8", spend=out[4])
    chain.send("b8", becomes_tip=False, disconnects=True)

    # a coinbase claiming a satoshi too many
    #                                                    \-> b9 (4)
    chain.move_tip("b6")
    chain.next_block("b9", spend=out[4], additional_coinbase_value=1)
    chain.reject("b9", "bad-cb-amount")

    # the same, at the end of a longer chain
    #                                          \-> b10 (3) -> b11 (4)
    chain.move_tip("b5")
    chain.next_block("b10", spend=out[3])
    chain.send("b10", becomes_tip=False)
    chain.next_block("b11", spend=out[4], additional_coinbase_value=1)
    chain.reject("b11", "bad-cb-amount")

    # the same, at the end of a valid fork the node re-orgs to
    #                                          \-> b12 (3) -> b13 (4) -> b14 (5)
    chain.move_tip("b5")
    chain.next_block("b12", spend=out[3])
    chain.save_spendable_output()
    chain.next_block("b13", spend=out[4])
    chain.save_spendable_output()
    chain.next_block("b14", spend=out[5], additional_coinbase_value=1)
    chain.send("b12", becomes_tip=False)
    chain.send("b13")
    chain.reject("b14", "bad-cb-amount")
    chain.steps.append(TipStep(name="b13", block_hash=chain.blocks["b13"].header.hash))


def _sigops_and_spends(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b15` to `b22`: sigops, and spends off the chain."""
    # MAX_BLOCK_SIGOPS - 1 checksigs, and one in the coinbase
    #     -> b12 (3) -> b13 (4) -> b15 (5) -> b16 (6)
    chain.move_tip("b13")
    lots_of_checksigs = _OP_CHECKSIG * (_MAX_BLOCK_SIGOPS - 1)
    chain.next_block("b15", spend=out[5], script=lots_of_checksigs)
    chain.save_spendable_output()
    chain.send("b15")

    too_many_checksigs = _OP_CHECKSIG * _MAX_BLOCK_SIGOPS
    chain.next_block("b16", spend=out[6], script=too_many_checksigs)
    chain.reject("b16", "bad-blk-sigops")

    # a spend of a transaction re-orged out
    #                                -> b15 (5) -> b17 (b3.vtx[1])
    txout_b3 = chain.blocks["b3"].transactions[1]
    chain.move_tip("b15")
    chain.next_block("b17", spend=txout_b3)
    chain.reject("b17", "bad-txns-inputs-missingorspent")

    # the same, on a fork
    #                      \-> b18 (b3.vtx[1]) -> b19 (6)
    chain.move_tip("b13")
    chain.next_block("b18", spend=txout_b3)
    chain.send("b18", becomes_tip=False)
    chain.next_block("b19", spend=out[6])
    chain.reject("b19", "bad-txns-inputs-missingorspent")

    # a spend of an immature coinbase
    #                                -> b15 (5) -> b20 (7)
    chain.move_tip("b15")
    chain.next_block("b20", spend=out[7])
    chain.reject("b20", "bad-txns-premature-spend-of-coinbase")

    # the same, on a fork
    #                      \-> b21 (6) -> b22 (5)
    chain.move_tip("b13")
    chain.next_block("b21", spend=out[6])
    chain.send("b21", becomes_tip=False)
    chain.next_block("b22", spend=out[5])
    chain.reject("b22", "bad-txns-premature-spend-of-coinbase")


def _weight_and_coinbase_length(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b23` to `b30`: the weight, and the coinbase's length."""
    # a block of MAX_BLOCK_WEIGHT
    #                                -> b15 (5) -> b23 (6)
    chain.move_tip("b15")
    b23 = chain.next_block("b23", spend=out[6])
    b23 = chain.update_block("b23", [_padding_tx(b23)])
    _check(b23.weight == MAX_BLOCK_WEIGHT, "b23 is not of MAX_BLOCK_WEIGHT")
    chain.send("b23")
    chain.save_spendable_output()

    # and one of MAX_BLOCK_WEIGHT + 4, then a child of it
    #                                           \-> b24 (6) -> b25 (7)
    chain.move_tip("b15")
    b24 = chain.next_block("b24", spend=out[6])
    # Core reuses b23's padding, which spends b23's second transaction
    padding = _padding_tx(b24, extra=1, spend=b23.transactions[1])
    b24 = chain.update_block("b24", [padding])
    _check(b24.weight == MAX_BLOCK_WEIGHT + 4, "b24 is not four over")
    chain.reject("b24", "bad-blk-length")
    chain.next_block("b25", spend=out[7])
    chain.send("b25", becomes_tip=False)

    # a coinbase scriptSig of one octet, then a child
    chain.move_tip("b15")
    b26 = chain.next_block("b26", spend=out[6])
    chain.update_coinbase(
        "b26",
        _coinbase(
            chain.height("b26"),
            value=b26.transactions[0].vout[0].value,
            script_sig=b"\x00",
        ),
    )
    chain.reject("b26", "bad-cb-length")
    chain.next_block("b27", spend=out[7])
    chain.send("b27", becomes_tip=False)

    # one of a hundred and one octets, then a child
    chain.move_tip("b15")
    b28 = chain.next_block("b28", spend=out[6])
    chain.update_coinbase(
        "b28",
        _coinbase(
            chain.height("b28"),
            value=b28.transactions[0].vout[0].value,
            script_sig=b"\x00" * 101,
        ),
    )
    chain.reject("b28", "bad-cb-length")
    chain.next_block("b29", spend=out[7])
    chain.send("b29", becomes_tip=False)

    # and one of a hundred
    #                                -> b23 (6) -> b30 (7)
    chain.move_tip("b23")
    b30 = chain.next_block("b30")
    height = chain.height("b30")
    script_sig = _coinbase_script_sig(height)
    script_sig += b"\x00" * (100 - len(script_sig))
    chain.update_coinbase(
        "b30",
        _coinbase(
            height, value=b30.transactions[0].vout[0].value, script_sig=script_sig
        ),
    )
    chain.send("b30")
    chain.save_spendable_output()


def _checkmultisig_sigops(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b31` to `b38`: multisig sigops, and a failed block."""
    #     -> b30 (7) -> b31 (8) -> b33 (9) -> b35 (10)
    #                                                 \-> b36 (11)
    #                                     \-> b34 (10)
    #                         \-> b32 (9)
    # each OP_CHECKMULTISIG counts twenty
    lots_of_multisigs = (
        _OP_CHECKMULTISIG * ((_MAX_BLOCK_SIGOPS - 1) // 20) + _OP_CHECKSIG * 19
    )
    b31 = chain.next_block("b31", spend=out[8], script=lots_of_multisigs)
    _check(_legacy_sig_op_count(b31) == _MAX_BLOCK_SIGOPS, "b31 is not at the bound")
    chain.send("b31")
    chain.save_spendable_output()

    # one over the bound, the coinbase holding the one
    too_many_multisigs = _OP_CHECKMULTISIG * (_MAX_BLOCK_SIGOPS // 20)
    b32 = chain.next_block("b32", spend=out[9], script=too_many_multisigs)
    _check(_legacy_sig_op_count(b32) == _MAX_BLOCK_SIGOPS + 1, "b32 is not one over")
    chain.reject("b32", "bad-blk-sigops")

    chain.move_tip("b31")
    lots_of_multisigs = (
        _OP_CHECKMULTISIGVERIFY * ((_MAX_BLOCK_SIGOPS - 1) // 20) + _OP_CHECKSIG * 19
    )
    chain.next_block("b33", spend=out[9], script=lots_of_multisigs)
    chain.send("b33")
    chain.save_spendable_output()

    too_many_multisigs = _OP_CHECKMULTISIGVERIFY * (_MAX_BLOCK_SIGOPS // 20)
    chain.next_block("b34", spend=out[10], script=too_many_multisigs)
    chain.reject("b34", "bad-blk-sigops")

    chain.move_tip("b33")
    lots_of_checksigs = _OP_CHECKSIGVERIFY * (_MAX_BLOCK_SIGOPS - 1)
    chain.next_block("b35", spend=out[10], script=lots_of_checksigs)
    chain.send("b35")
    chain.save_spendable_output()

    too_many_checksigs = _OP_CHECKSIGVERIFY * _MAX_BLOCK_SIGOPS
    chain.next_block("b36", spend=out[11], script=too_many_checksigs)
    chain.reject("b36", "bad-blk-sigops")

    # b37 spends out[11] twice, then b38 spends b37's first transaction
    #                                                 \-> b37 (11)
    #                                                 \-> b38 (11/37)
    chain.move_tip("b35")
    b37 = chain.next_block("b37", spend=out[11])
    txout_b37 = b37.transactions[1]
    chain.update_block("b37", [chain.create_and_sign(out[11], 0)])
    chain.reject("b37", "bad-txns-inputs-missingorspent")

    chain.move_tip("b35")
    chain.next_block("b38", spend=txout_b37)
    chain.reject("b38", "bad-txns-inputs-missingorspent")


def _p2sh_sigops(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b39` to `b43`: the sigops of P2SH redeem scripts."""
    #     -> b35 (10) -> b39 (11) -> b41 (12)
    #                            \-> b40 (12)
    # b39 is full of P2SH outputs whose redeem script counts six sigops
    chain.move_tip("b35")
    chain.next_block("b39")
    sigops_per_output = 6
    redeem_script = script_serialize(
        [_KEY.pub.sec, *["OP_2DUP", "OP_CHECKSIGVERIFY"] * 5, "OP_CHECKSIG"]
    )
    p2sh_script = ScriptPubKey.p2sh(redeem_script, "regtest").script

    # a satoshi to the P2SH script, the rest to OP_TRUE
    spend = out[11]
    tx = _create_tx(spend, 0, 1, p2sh_script)
    tx = _with_outputs(tx, TxOut(spend.vout[0].value - 1, _OP_TRUE))
    tx = chain.sign_tx(tx, spend)
    b39 = chain.update_block("b39", [tx])
    b39_outputs = 1

    # then the same off each OP_TRUE output, until the block is full
    transactions = list(b39.transactions)
    tx_last = tx
    total_weight = b39.weight
    while True:
        tx_new = _create_tx(tx_last, 1, 1, p2sh_script)
        tx_new = _with_outputs(tx_new, TxOut(tx_last.vout[1].value - 1, _OP_TRUE))
        total_weight += tx_new.weight
        if total_weight >= MAX_BLOCK_WEIGHT:
            break
        transactions.append(tx_new)
        tx_last = tx_new
        b39_outputs += 1

    # the sum above leaves out the transaction count's own length. Core
    # drops the last transactions where that reaches the bound, which its
    # random signature lengths can make happen and these fixed ones do not
    _check(
        Block(b39.header, transactions, check_validity=False).weight < MAX_BLOCK_WEIGHT,
        "b39 is not under MAX_BLOCK_WEIGHT",
    )
    b39 = chain.rebuild("b39", transactions)
    chain.send("b39")
    chain.save_spendable_output()

    # b40 spends enough of them to go one sigop over the bound
    chain.move_tip("b39")
    b40 = chain.next_block("b40", spend=out[12])
    sigops = _legacy_sig_op_count(b40)
    num_txs = (_MAX_BLOCK_SIGOPS - sigops) // sigops_per_output
    _check(num_txs <= b39_outputs, "b39 holds too few P2SH outputs")

    last_out_point = OutPoint(b40.transactions[1].id, 0)
    redeem_push = script_serialize([redeem_script])
    new_txs = []
    for i in range(1, num_txs + 1):
        tx = Tx(
            _TX_VERSION,
            0,
            [
                TxIn(last_out_point, b"", 0, check_validity=False),
                TxIn(
                    OutPoint(b39.transactions[i].id, 0),
                    redeem_push,
                    0,
                    check_validity=False,
                ),
            ],
            [TxOut(1, _OP_TRUE)],
            check_validity=False,
        )
        tx = _with_script_sig(
            tx, 1, chain.signature_push(tx, 1, redeem_script) + redeem_push
        )
        new_txs.append(tx)
        last_out_point = OutPoint(tx.id, 0)

    b40_sigops_to_fill = _MAX_BLOCK_SIGOPS - (num_txs * sigops_per_output + sigops) + 1
    filler = Tx(
        _TX_VERSION,
        0,
        [TxIn(last_out_point, b"", 0, check_validity=False)],
        [TxOut(1, _OP_CHECKSIG * b40_sigops_to_fill, check_validity=False)],
        check_validity=False,
    )
    b40 = chain.update_block("b40", [*new_txs, filler])
    chain.reject("b40", "bad-blk-sigops")

    # b41 is b40 one sigop short
    chain.move_tip("b39")
    chain.next_block("b41")
    filler = Tx(
        _TX_VERSION,
        0,
        [TxIn(last_out_point, b"", 0, check_validity=False)],
        [TxOut(1, _OP_CHECKSIG * (b40_sigops_to_fill - 1), check_validity=False)],
        check_validity=False,
    )
    chain.update_block("b41", [*b40.transactions[1:-1], filler])
    chain.send("b41")

    # a common base again: b42 ties b41, seen first, and b43 is longer
    #     -> b39 (11) -> b42 (12) -> b43 (13)
    chain.move_tip("b39")
    chain.next_block("b42", spend=out[12])
    chain.save_spendable_output()
    chain.next_block("b43", spend=out[13])
    chain.save_spendable_output()
    chain.send("b42", becomes_tip=False)
    chain.send("b43")


def _malformed(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b44` to `b52`: blocks built by hand to be malformed."""
    #     -> b43 (13) -> b44 (14)
    #                           \-> ??? (15)
    tip = chain.blocks["b43"]
    height = chain.height("b43") + 1
    chain.signing_for = "b44"
    b44 = _make_block(
        tip.header.hash,
        [_coinbase(height), chain.create_and_sign(out[14], 1)],
        _time_of(tip) + 1,
    )
    chain._register("b44", b44, height)
    chain.send("b44")

    # a non-coinbase as the first transaction
    b45 = _make_block(b44.header.hash, [_create_tx(out[15], 0, 1)], _time_of(b44) + 1)
    chain._register("b45", b45, height + 1)
    chain.reject("b45", "bad-cb-missing")

    # no transaction at all
    chain.move_tip("b44")
    b46 = _make_block(b44.header.hash, [], _time_of(b44) + 1)
    chain._register("b46", b46, height + 1)
    chain.reject("b46", "bad-blk-length")

    # a hash over the target
    chain.move_tip("b44")
    b47 = chain.next_block("b47")
    header = b47.header
    target = int.from_bytes(header.target, "big")
    nonce = header.nonce
    while int.from_bytes(header.hash, "big") <= target:
        nonce += 1
        header = BlockHeader(
            header.version,
            header.previous_block_hash,
            header.merkle_root,
            header.time,
            header.bits,
            nonce,
            check_validity=False,
        )
    b47 = Block(header, b47.transactions, check_validity=False)
    chain.reject("b47", "high-hash", unrequested=True, block=b47)

    # a timestamp three hours past the start, ahead of the node's clock
    chain.move_tip("b44")
    chain.next_block("b48")
    b48 = chain.rebuild(
        "b48", chain.blocks["b48"].transactions, time=chain.start_time + 3 * 60 * 60
    )
    _check(
        _time_of(b48) > chain.start_time + MAX_FUTURE_BLOCK_TIME,
        "b48 is not too far ahead",
    )
    chain.reject("b48", "time-too-new", unrequested=True, disconnects=False)

    # a merkle root off by one
    chain.move_tip("b44")
    b49 = chain.next_block("b49")
    merkle_root = int.from_bytes(b49.header.merkle_root, "big") + 1
    b49 = _with_header(b49, merkle_root=merkle_root.to_bytes(32, "big"))
    chain.reject("b49", "bad-txnmrklroot", block=b49)

    # a target other than the chain's
    chain.move_tip("b44")
    b50 = chain.next_block("b50")
    bits = int.from_bytes(b50.header.bits, "big") - 1
    b50 = _with_header(b50, bits=bits.to_bytes(4, "big"))
    chain.reject("b50", "bad-diffbits", unrequested=True, block=b50)

    # two coinbases
    chain.move_tip("b44")
    chain.next_block("b51")
    chain.update_block("b51", [_coinbase(51)])
    chain.reject("b51", "bad-cb-multiple")

    # a transaction twice, where the merkle tree pairs the two
    chain.move_tip("b44")
    b52 = chain.next_block("b52", spend=out[15])
    tx = _create_tx(b52.transactions[1], 0, 1)
    chain.update_block("b52", [tx, tx])
    chain.reject("b52", "bad-txns-duplicate")


def _timestamps(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b53` to `b48p`: the median time past, and the clock."""
    #     -> b43 (13) -> b53 (14) -> b55 (15)
    #                            \-> b54 (15)
    #               -> b44 (14) -> b48 () -> b48p ()
    chain.move_tip("b43")
    chain.next_block("b53", spend=out[14])
    chain.send("b53", becomes_tip=False)
    chain.save_spendable_output()

    b35_time = _time_of(chain.blocks["b35"])
    chain.next_block("b54", spend=out[15])
    chain.rebuild("b54", chain.blocks["b54"].transactions, time=b35_time - 1)
    chain.reject("b54", "time-too-old", unrequested=True)

    chain.move_tip("b53")
    chain.next_block("b55", spend=out[15])
    chain.update_block("b55", [], time=b35_time)
    chain.send("b55")
    chain.save_spendable_output()

    # b48, refused as too far ahead, is taken two hours on, and its child
    # makes its chain the longest; that child is then invalidated
    chain.steps.append(MockTimeStep(timestamp=chain.start_time + 2 * 60 * 60))
    chain.move_tip("b48")
    chain.next_block("b48p")
    chain.send("b48", becomes_tip=False)
    chain.send("b48p")
    chain.steps.append(
        InvalidateStep(name="b48p", block_hash=chain.blocks["b48p"].header.hash)
    )
    chain.steps.append(MockTimeStep(timestamp=0))


def _merkle_malleability(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b56` to `b60`: CVE-2012-2459, and invalid spends."""
    #     -> b55 (15) -> b57p2 (16)
    #                \-> b57   (16)
    #                \-> b56p2 (16)
    #                \-> b56   (16)
    # b56 is b57 with its last transaction repeated: the same merkle
    # root, so the same hash
    chain.move_tip("b55")
    chain.next_block("b57")
    tx = chain.create_and_sign(out[16], 1)
    tx1 = _create_tx(tx, 0, 1)
    b57 = chain.update_block("b57", [tx, tx1])

    chain.move_tip("b55")
    b56 = Block(b57.header, [*b57.transactions, tx1], check_validity=False)
    merkle_root, _ = merkle_root_and_mutated_from_transactions(b56.transactions)
    _check(merkle_root == b57.header.merkle_root, "b56 commits to another root")
    chain._register("b56", b56, chain.height("b57"))
    chain.reject("b56", "bad-txns-duplicate")

    # b56p2 is b57p2 with its last two transactions repeated, neither copy
    # next to its original
    chain.move_tip("b55")
    chain.next_block("b57p2")
    tx = chain.create_and_sign(out[16], 1)
    tx1 = _create_tx(tx, 0, 1)
    tx2 = _create_tx(tx1, 0, 1)
    tx3 = _create_tx(tx2, 0, 1)
    tx4 = _create_tx(tx3, 0, 1)
    b57p2 = chain.update_block("b57p2", [tx, tx1, tx2, tx3, tx4])

    chain.move_tip("b55")
    b56p2 = Block(b57p2.header, [*b57p2.transactions, tx3, tx4], check_validity=False)
    merkle_root, _ = merkle_root_and_mutated_from_transactions(b56p2.transactions)
    _check(merkle_root == b57p2.header.merkle_root, "b56p2 commits to another root")
    chain._register("b56p2", b56p2, chain.height("b57p2"))
    chain.reject("b56p2", "bad-txns-duplicate")

    chain.move_tip("b57p2")
    chain.send("b57p2")

    # seen second, so not the tip
    chain.move_tip("b57")
    chain.send("b57", becomes_tip=False)
    chain.save_spendable_output()

    #     -> b57 (16) -> b60 ()
    #                \-> ??? (17)
    # an input naming an output its transaction does not have
    chain.move_tip("b57")
    chain.next_block("b58", spend=out[17])
    _check(len(out[17].vout) < 42, "out[17] has an output 42")
    tx = Tx(
        _TX_VERSION,
        0,
        [
            TxIn(
                OutPoint(out[17].id, 42), _OP_TRUE, SEQUENCE_FINAL, check_validity=False
            )
        ],
        [TxOut(0, b"", check_validity=False)],
        check_validity=False,
    )
    chain.update_block("b58", [tx])
    chain.reject("b58", "bad-txns-inputs-missingorspent")

    # outputs worth more than the inputs
    chain.move_tip("b57")
    chain.next_block("b59")
    chain.update_block("b59", [chain.create_and_sign(out[17], 51 * _COIN)])
    chain.reject("b59", "bad-txns-in-belowout")

    chain.move_tip("b57")
    chain.next_block("b60")
    chain.send("b60")
    chain.save_spendable_output()


def _duplicate_coinbase(chain: _Chain, out: list[Tx], duplicate_tx: Tx) -> None:
    """Core's run from `b61` to `b63`: BIP30, and non-final transactions."""
    #     -> b57 (16) -> b60 () -> b61 ()
    # BIP30: the coinbase of `dup_cb` again, while its output is unspent
    chain.move_tip("b60")
    b61 = chain.next_block("b61")
    _check(chain.height("b61") == _DUPLICATE_COINBASE_HEIGHT, "b61 is not at 120")
    b61 = chain.update_coinbase(
        "b61",
        _coinbase(
            _DUPLICATE_COINBASE_HEIGHT,
            value=b61.transactions[0].vout[0].value,
            lock_time=0,
            script_sig=_DUPLICATE_COINBASE_SCRIPT_SIG,
        ),
    )
    _check(
        b61.transactions[0].serialize(False) == duplicate_tx.serialize(False),
        "b61's coinbase is not dup_cb's",
    )
    chain.reject("b61", "bad-txns-BIP30")

    # and once that output is spent
    #     -> b57 (16) -> b_spend_dup_cb (b_dup_cb) -> b_dup_2 ()
    chain.move_tip("b57")
    chain.next_block("spend_dup_cb")
    tx = Tx(
        _TX_VERSION,
        0,
        [TxIn(OutPoint(duplicate_tx.id, 0), b"", 0, check_validity=False)],
        [TxOut(0, _OP_TRUE)],
        check_validity=False,
    )
    chain.update_block("spend_dup_cb", [chain.sign_tx(tx, duplicate_tx)])

    b_dup_2 = chain.next_block("dup_2")
    b_dup_2 = chain.update_coinbase(
        "dup_2",
        _coinbase(
            _DUPLICATE_COINBASE_HEIGHT,
            value=b_dup_2.transactions[0].vout[0].value,
            lock_time=0,
            script_sig=_DUPLICATE_COINBASE_SCRIPT_SIG,
        ),
    )
    _check(
        b_dup_2.transactions[0].serialize(False) == duplicate_tx.serialize(False),
        "dup_2's coinbase is not dup_cb's",
    )
    chain.steps.append(
        CoinStep(
            tx_id=duplicate_tx.id,
            vout=0,
            confirmations=_DUPLICATE_COINBASE_HEIGHT - 1,
        )
    )
    chain.send("spend_dup_cb", becomes_tip=False)
    chain.send("dup_2")
    chain.steps.append(CoinStep(tx_id=duplicate_tx.id, vout=0, confirmations=1))

    #     -> b_dup_2 () -> b62 (18)
    # a lock time not yet reached, the input not final
    chain.move_tip("dup_2")
    chain.next_block("b62")
    tx = Tx(
        _TX_VERSION,
        0xFFFFFFFF,
        [TxIn(OutPoint(out[18].id, 0), b"", 0, check_validity=False)],
        [TxOut(0, _OP_TRUE)],
        check_validity=False,
    )
    chain.update_block("b62", [tx])
    chain.reject("b62", "bad-txns-nonfinal")

    # the same, in the coinbase
    chain.move_tip("dup_2")
    b63 = chain.next_block("b63")
    chain.update_coinbase(
        "b63",
        _coinbase(
            chain.height("b63"),
            value=b63.transactions[0].vout[0].value,
            lock_time=0xFFFFFFFF,
            sequence=0xDEADBEEF,
        ),
    )
    chain.reject("b63", "bad-txns-nonfinal")


def _bloated_and_spends(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b64a` to `b72`: a bloated block, spends, the subsidy."""
    #     -> b_dup_2 () -> b64 (18)
    #                 \-> b64a (18)
    # b64a is b64 with its transaction count in nine octets: over
    # MAX_BLOCK_WEIGHT that way, and at it the canonical way
    chain.move_tip("dup_2")
    regular = chain.next_block("b64a", spend=out[18])
    b64 = chain.update_block("b64a", [_padding_tx(regular)])
    bloated = b64.header.serialize(check_validity=False)
    bloated += _BLOATED_COUNT_PREFIX + len(b64.transactions).to_bytes(8, "little")
    bloated += b"".join(
        tx.serialize(False, check_validity=False) for tx in b64.transactions
    )
    _check(
        len(bloated) * WITNESS_SCALE_FACTOR == MAX_BLOCK_WEIGHT + 8 * 4,
        "b64a is not eight octets over",
    )
    chain.send(
        "b64a",
        becomes_tip=False,
        reject_reason="non-canonical ReadCompactSize()",
        serialized=bloated,
    )

    chain.move_tip("dup_2")
    _check(b64.weight == MAX_BLOCK_WEIGHT, "b64 is not of MAX_BLOCK_WEIGHT")
    chain._register("b64", b64, chain.height("b64a"))
    chain.send("b64")
    chain.save_spendable_output()

    #     -> b64 (18) -> b65 (19)
    # a transaction spending one before it in the block
    chain.move_tip("b64")
    chain.next_block("b65")
    tx1 = chain.create_and_sign(out[19], out[19].vout[0].value)
    tx2 = chain.create_and_sign(tx1, 0)
    chain.update_block("b65", [tx1, tx2])
    chain.send("b65")
    chain.save_spendable_output()

    #                \-> b66 (20)
    # one spending one after it
    chain.move_tip("b65")
    chain.next_block("b66")
    tx1 = chain.create_and_sign(out[20], out[20].vout[0].value)
    tx2 = chain.create_and_sign(tx1, 1)
    chain.update_block("b66", [tx2, tx1])
    chain.reject("b66", "bad-txns-inputs-missingorspent")

    #                \-> b67 (20)
    # two spending the same one
    chain.move_tip("b65")
    chain.next_block("b67")
    tx1 = chain.create_and_sign(out[20], out[20].vout[0].value)
    tx2 = chain.create_and_sign(tx1, 1)
    tx3 = chain.create_and_sign(tx1, 2)
    chain.update_block("b67", [tx1, tx2, tx3])
    chain.reject("b67", "bad-txns-inputs-missingorspent")

    #     -> b65 (19) -> b69 (20)
    #                \-> b68 (20)
    # the coinbase claims ten satoshis over the subsidy: b68 pays nine in
    # fees, b69 ten
    chain.move_tip("b65")
    chain.next_block("b68", additional_coinbase_value=10)
    tx = chain.create_and_sign(out[20], out[20].vout[0].value - 9)
    chain.update_block("b68", [tx])
    chain.reject("b68", "bad-cb-amount")

    chain.move_tip("b65")
    chain.next_block("b69", additional_coinbase_value=10)
    tx = chain.create_and_sign(out[20], out[20].vout[0].value - 10)
    chain.update_block("b69", [tx])
    chain.send("b69")
    chain.save_spendable_output()

    #                \-> b70 (21)
    # an input naming a transaction that does not exist
    chain.move_tip("b69")
    chain.next_block("b70", spend=out[21])
    tx = Tx(
        _TX_VERSION,
        0,
        [TxIn(OutPoint(_BOGUS_TX_ID, 0), b"", SEQUENCE_FINAL, check_validity=False)],
        [TxOut(1, b"", check_validity=False)],
        check_validity=False,
    )
    chain.update_block("b70", [tx])
    chain.reject("b70", "bad-txns-inputs-missingorspent")

    #     -> b69 (20) -> b72 (21)
    #                \-> b71 (21)
    # b71 is b72 with its last transaction repeated, under the same hash
    chain.move_tip("b69")
    chain.next_block("b72")
    tx1 = chain.create_and_sign(out[21], 2)
    tx2 = chain.create_and_sign(tx1, 1)
    b72 = chain.update_block("b72", [tx1, tx2])
    b71 = Block(b72.header, [*b72.transactions, tx2], check_validity=False)
    chain._register("b71", b71, chain.height("b69") + 1)
    chain.reject("b71", "bad-txns-duplicate")

    chain.move_tip("b72")
    chain.send("b72")
    chain.save_spendable_output()


def _sigops_around_pushes(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b73` to `b76`: sigops before and after a push."""
    #     -> b72 (21) -> b75 (22) -> b76 (23)
    #                \-> b73, b74 (22)
    # b73: sigops after an element over MAX_SCRIPT_ELEMENT_SIZE count
    chain.move_tip("b72")
    chain.next_block("b73")
    size = _MAX_BLOCK_SIGOPS - 1 + MAX_SCRIPT_ELEMENT_SIZE + 1 + 5 + 1
    script = bytearray(_OP_CHECKSIG * size)
    script[_MAX_BLOCK_SIGOPS - 1] = _OP_PUSHDATA4
    element_size = MAX_SCRIPT_ELEMENT_SIZE + 1
    script[_MAX_BLOCK_SIGOPS : _MAX_BLOCK_SIGOPS + 4] = element_size.to_bytes(
        4, "little"
    )
    b73 = chain.update_block("b73", [chain.create_and_sign(out[22], 1, bytes(script))])
    _check(_legacy_sig_op_count(b73) == _MAX_BLOCK_SIGOPS + 1, "b73 is not one over")
    chain.reject("b73", "bad-blk-sigops")

    # b74 and b75: sigops before a push running past the script's end
    # count, those inside it do not
    chain.move_tip("b72")
    chain.next_block("b74")
    size = _MAX_BLOCK_SIGOPS - 1 + MAX_SCRIPT_ELEMENT_SIZE + 42
    script = bytearray(_OP_CHECKSIG * size)
    script[_MAX_BLOCK_SIGOPS] = _OP_PUSHDATA4
    script[_MAX_BLOCK_SIGOPS + 1 : _MAX_BLOCK_SIGOPS + 5] = b"\xfe\xff\xff\xff"
    chain.update_block("b74", [chain.create_and_sign(out[22], 1, bytes(script))])
    chain.reject("b74", "bad-blk-sigops")

    chain.move_tip("b72")
    chain.next_block("b75")
    script = bytearray(_OP_CHECKSIG * size)
    script[_MAX_BLOCK_SIGOPS - 1] = _OP_PUSHDATA4
    script[_MAX_BLOCK_SIGOPS : _MAX_BLOCK_SIGOPS + 4] = b"\xff\xff\xff\xff"
    chain.update_block("b75", [chain.create_and_sign(out[22], 1, bytes(script))])
    chain.send("b75")
    chain.save_spendable_output()

    # b76: an element made of OP_CHECKSIG octets counts none
    chain.move_tip("b75")
    chain.next_block("b76")
    size = _MAX_BLOCK_SIGOPS - 1 + MAX_SCRIPT_ELEMENT_SIZE + 1 + 5
    script = bytearray(_OP_CHECKSIG * size)
    script[_MAX_BLOCK_SIGOPS - 1] = _OP_PUSHDATA4
    chain.update_block("b76", [chain.create_and_sign(out[23], 1, bytes(script))])
    chain.send("b76")
    chain.save_spendable_output()


def _resurrection_and_op_return(chain: _Chain, out: list[Tx]) -> None:
    """Core's run from `b77` to `b_cb34`: re-orgs, dead paths, headers."""
    #     -> b77 (24) -> b78 (25) -> b79 (26)
    #                \-> b80 (25) -> b81 (26) -> b82 (27)
    # b78 and b79 hold a chain of spends, standard so that the mempool
    # takes them back once the re-org to b82 disconnects them
    chain.move_tip("b76")
    chain.next_block("b77")
    tx77 = chain.create_and_sign(out[24], 10 * _COIN, _COINBASE_SCRIPT)
    chain.update_block("b77", [tx77])
    chain.send("b77")
    chain.save_spendable_output()

    chain.next_block("b78")
    tx78 = chain.create_and_sign(tx77, 9 * _COIN, _COINBASE_SCRIPT)
    chain.update_block("b78", [tx78])
    chain.send("b78")

    chain.next_block("b79")
    tx79 = chain.create_and_sign(tx78, 8 * _COIN, _COINBASE_SCRIPT)
    chain.update_block("b79", [tx79])
    chain.send("b79")
    chain.steps.append(MempoolStep(tx_ids=frozenset()))

    chain.move_tip("b77")
    chain.next_block("b80", spend=out[25])
    chain.send("b80", becomes_tip=False, unrequested=True)
    chain.save_spendable_output()

    chain.next_block("b81", spend=out[26])
    chain.send("b81", becomes_tip=False, unrequested=True)
    chain.save_spendable_output()

    chain.next_block("b82", spend=out[27])
    chain.send("b82")
    chain.save_spendable_output()
    chain.steps.append(MempoolStep(tx_ids=frozenset({tx78.id, tx79.id})))

    #     -> b81 (26) -> b82 (27) -> b83 (28)
    # an invalid op code on a branch the script does not take
    chain.next_block("b83")
    script = (
        script_serialize(["OP_IF"])
        + _OP_INVALIDOPCODE
        + script_serialize(["OP_ELSE", "OP_TRUE", "OP_ENDIF"])
    )
    tx1 = chain.create_and_sign(out[28], out[28].vout[0].value, script)
    tx2 = _with_script_sig(
        _create_tx(tx1, 0, 0, _OP_TRUE), 0, script_serialize(["OP_FALSE"])
    )
    chain.update_block("b83", [tx1, tx2])
    chain.send("b83")
    chain.save_spendable_output()

    # b83 (28) -> b84 (29) -> b87 (30) -> b88 (31)
    #        \-> b85 (29) -> b86 (30)             \-> b89a (32)
    # re-orgs off and back onto a block whose outputs include OP_RETURN
    chain.next_block("b84")
    tx1 = _create_tx(out[29], 0, 0, _OP_RETURN)
    tx1 = _with_outputs(tx1, *[TxOut(0, _OP_TRUE)] * 4)
    tx1 = chain.sign_tx(tx1, out[29])
    tx2 = _with_outputs(_create_tx(tx1, 1, 0, _OP_RETURN), TxOut(0, _OP_RETURN))
    tx3 = _with_outputs(_create_tx(tx1, 2, 0, _OP_RETURN), TxOut(0, _OP_TRUE))
    tx4 = _with_outputs(_create_tx(tx1, 3, 0, _OP_TRUE), TxOut(0, _OP_RETURN))
    tx5 = _create_tx(tx1, 4, 0, _OP_RETURN)
    chain.update_block("b84", [tx1, tx2, tx3, tx4, tx5])
    chain.send("b84")
    chain.save_spendable_output()

    chain.move_tip("b83")
    chain.next_block("b85", spend=out[29])
    chain.send("b85", becomes_tip=False)
    chain.next_block("b86", spend=out[30])
    chain.send("b86")

    chain.move_tip("b84")
    chain.next_block("b87", spend=out[30])
    chain.send("b87", becomes_tip=False)
    chain.save_spendable_output()
    chain.next_block("b88", spend=out[31])
    chain.send("b88")
    chain.save_spendable_output()

    # a spend of an OP_RETURN output
    chain.next_block("b89a", spend=out[32])
    chain.update_block("b89a", [_create_tx(tx1, 0, 0, _OP_TRUE)])
    chain.reject("b89a", "bad-txns-inputs-missingorspent")

    # a header version BIP34 retired
    chain.move_tip("b88")
    chain.next_block("b_v1", version=1)
    chain.reject("b_v1", "bad-version(0x00000001)", unrequested=True)

    # a coinbase whose height push is cut short
    chain.move_tip("b87")
    b_cb34 = chain.next_block("b_cb34")
    coinbase = b_cb34.transactions[0]
    chain.update_coinbase(
        "b_cb34",
        _coinbase(
            chain.height("b_cb34"),
            value=coinbase.vout[0].value,
            script_sig=coinbase.vin[0].script_sig[:-1],
        ),
    )
    chain.reject("b_cb34", "bad-cb-height")


def steps(
    start_time: int,
) -> list[BlockStep | TipStep | InvalidateStep | MockTimeStep | MempoolStep | CoinStep]:
    """Return Core's `feature_block` sequence, as steps a runner takes in order.

    :param start_time: the unix time the sequence starts at, Core's
        `time.time()`: the first block's timestamp is one second past it,
        and `b48` is three hours past it. A runner gives its node a clock
        near it.
    """
    chain = _Chain(start_time)

    # a coinbase valid at height 1 only because BIP34 is not yet enforced,
    # and the same transaction as a coinbase at height 120
    b_dup_cb = chain.next_block("dup_cb")
    b_dup_cb = chain.update_coinbase(
        "dup_cb", _coinbase(1, script_sig=_DUPLICATE_COINBASE_SCRIPT_SIG)
    )
    duplicate_tx = b_dup_cb.transactions[0]
    chain.send("dup_cb")

    # outputs of the largest valid script and of the smallest invalid one
    max_valid_script = script_serialize(
        [b"\x01" * MAX_SCRIPT_ELEMENT_SIZE] * 19 + [b"\x01" * 62]
    )
    _check(len(max_valid_script) == MAX_SCRIPT_SIZE, "not MAX_SCRIPT_SIZE")
    min_invalid_script = script_serialize(
        [b"\x01" * MAX_SCRIPT_ELEMENT_SIZE] * 19 + [b"\x01" * 63]
    )
    _check(len(min_invalid_script) == MAX_SCRIPT_SIZE + 1, "not one over")
    b0 = chain.next_block(
        "b0", additional_output_scripts=(max_valid_script, min_invalid_script)
    )
    chain.save_spendable_output()
    chain.send("b0")

    # the coinbases mature
    for i in range(_BUFFER_BLOCKS):
        chain.next_block(f"maturitybuffer.{i}")
        chain.save_spendable_output()
    for i in range(_BUFFER_BLOCKS):
        chain.send(f"maturitybuffer.{i}")
    buffer_tip = f"maturitybuffer.{_BUFFER_BLOCKS - 1}"

    # the output of the largest valid script is spendable: a block
    # spending it is taken, then invalidated, and the mempool does not
    # take the spend back
    chain.next_block("max_script_size")
    tx = Tx(
        _TX_VERSION,
        0,
        [TxIn(OutPoint(b0.transactions[0].id, 1), b"", 0, check_validity=False)],
        [TxOut(0, b"", check_validity=False)],
        check_validity=False,
    )
    chain.update_block("max_script_size", [tx])
    chain.send("max_script_size")
    chain.steps.append(
        InvalidateStep(
            name="max_script_size",
            block_hash=chain.blocks["max_script_size"].header.hash,
        )
    )
    chain.steps.append(MempoolStep(tx_ids=frozenset()))

    # the output of the smallest invalid one never entered the UTXO set
    chain.move_tip(buffer_tip)
    chain.next_block("max_script_size_plus_one")
    tx = Tx(
        _TX_VERSION,
        0,
        [TxIn(OutPoint(b0.transactions[0].id, 2), b"", 0, check_validity=False)],
        [TxOut(0, b"", check_validity=False)],
        check_validity=False,
    )
    chain.update_block("max_script_size_plus_one", [tx])
    chain.reject("max_script_size_plus_one", "bad-txns-inputs-missingorspent")
    chain.move_tip(buffer_tip)

    out = [chain.get_spendable_output() for _ in range(_OUTPUTS_TO_COLLECT)]

    _fork_and_reorg(chain, out)
    _sigops_and_spends(chain, out)
    _weight_and_coinbase_length(chain, out)
    _checkmultisig_sigops(chain, out)
    _p2sh_sigops(chain, out)
    _malformed(chain, out)
    _timestamps(chain, out)
    _merkle_malleability(chain, out)
    _duplicate_coinbase(chain, out, duplicate_tx)
    _bloated_and_spends(chain, out)
    _sigops_around_pushes(chain, out)
    _resurrection_and_op_return(chain, out)
    return chain.steps
