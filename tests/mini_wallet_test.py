# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`MiniWallet`, driven against a faked RPC rather than a spawned node.

`generate` runs the real block construction and the real proof-of-work
search (`build_coinbase`, `build_block`, `mine`): regtest's own target is
wide enough that a whole chain mines in well under a second, `node_test.py`'s
own `_FakeRpc` doctrine applied here to `submitblock`,
`sendrawtransaction`, `gettxout`, `getrawmempool` and `getrawtransaction`,
the only calls this module ever makes that touch a node for real.
"""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise
from typing import TYPE_CHECKING, override
from unittest.mock import PropertyMock, patch

import pytest
from btclib.block.block import Block, bip34_commitment
from btclib.block.mining import VERSION
from btclib.script import sig_hash
from btclib.script.script import parse
from btclib.script.script import serialize as script_serialize
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY
from ecc.curves.curve import secp256k1
from ecc.dsa import verify_

from btclib_functional_tests.capability import Capability
from btclib_functional_tests.mini_wallet import (
    DEFAULT_FEE_RATE,
    FEE,
    PADDING_DATACARRIER_SIZE,
    RAW_P2PK_SCRIPT_PUB_KEY,
    MiniWallet,
    Utxo,
    build_fork,
    build_next_block,
    nulldata_script_pub_key,
    raw_p2pk_script_sig,
)
from btclib_functional_tests.node import NodeAdapter

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Set as AbstractSet

_GENESIS = "00" * 32

# the vsize of every unpadded `create_self_transfer`, the figure Core's own
# `create_self_transfer` (`wallet.py`) prices its fee at for this coin shape
_SELF_TRANSFER_VSIZE = 104

# `DEFAULT_FEE_RATE` over that vsize, satoshis: 300 sat/vB times 104 vB
_DEFAULT_FEE = 31_200

# Core's own published address for the internal key 1 and a single OP_TRUE
# leaf (`test_framework/address.py`'s own
# `create_deterministic_address_bcrt1_p2tr_op_true`)
_ADDRESS_OP_TRUE = "bcrt1p9yfmy5h72durp7zrhlw9lf7jpwjgvwdg0jr0lqmmjtgg83266lqsekaqka"


class _FakeRpc:
    """A `bitcoin_core_rpc.BitcoinCoreRpcClient` stand-in: scripted answers.

    :param best_hash: what `getbestblockhash` answers.
    :param height: what `getblockcount` answers.
    :param median_time: what `getblockchaininfo`'s own `mediantime` answers
        -- a chain this wallet did not itself mine, the way the
        session-scoped `bitcoind_adapter` fixture hands more than one
        `MiniWallet` the same node, already has one.
    :param submit_answer: what every `submitblock` answers; `None` is
        acceptance, matching bitcoind's own convention.
    :param send_answer: what `sendrawtransaction` answers; a fixed value
        that overrides the sent tx's own id where given, so that
        `send_self_transfer`'s own mismatch check can be exercised.

    `txouts` maps a `(txid, vout)` pair to what `gettxout` answers for it,
    `None` -- the coin not in the UTXO set -- for any pair it lacks;
    `asked` records every pair `gettxout` was called for.

    `mempool` maps a hex txid to the hex `getrawtransaction` answers for
    it; `getrawmempool` answers its keys, and `mempool_reads` counts the
    `getrawmempool` calls. Nothing adds to it on its own: a test puts in
    it what a node's mempool would hold.
    """

    def __init__(
        self,
        *,
        best_hash: str = _GENESIS,
        height: int = 0,
        median_time: int = 0,
        submit_answer: object = None,
        send_answer: object = "sentinel",
    ) -> None:
        self._best_hash = best_hash
        self._height = height
        self._median_time = median_time
        self._submit_answer = submit_answer
        self._send_answer = send_answer
        self.submitted: list[str] = []
        self.sent: list[str] = []
        self.txouts: dict[tuple[str, int], object] = {}
        self.asked: list[tuple[str, int]] = []
        self.mempool: dict[str, object] = {}
        self.mempool_reads = 0

    def call(self, method: str, params: list[object] | None = None) -> object:
        tip_answers = {
            "getbestblockhash": self._best_hash,
            "getblockcount": self._height,
            "getblockchaininfo": {"mediantime": self._median_time},
        }
        if method in tip_answers:
            return tip_answers[method]
        if method == "submitblock":
            assert params is not None
            block_hex = params[0]
            assert isinstance(block_hex, str)
            self.submitted.append(block_hex)
            return self._submit_answer
        if method == "gettxout":
            assert params is not None
            txid, vout, include_mempool = params
            assert isinstance(txid, str)
            assert isinstance(vout, int)
            assert include_mempool is False
            self.asked.append((txid, vout))
            return self.txouts.get((txid, vout))
        if method in {"getrawmempool", "getrawtransaction"}:
            return self._mempool_call(method, params)
        # the only call left this fake ever answers: sendrawtransaction
        assert method == "sendrawtransaction"
        assert params is not None
        tx_hex, maxfeerate = params
        assert isinstance(tx_hex, str)
        # `wallet.py`'s own `sendrawtransaction` lifts the fee ceiling
        assert maxfeerate == 0
        self.sent.append(tx_hex)
        if self._send_answer == "sentinel":
            return _txid_of(tx_hex)
        return self._send_answer

    def _mempool_call(self, method: str, params: list[object] | None) -> object:
        """Answer `getrawmempool` or `getrawtransaction` off `mempool`."""
        if method == "getrawmempool":
            assert not params
            self.mempool_reads += 1
            return list(self.mempool)
        assert params is not None
        (txid,) = params
        assert isinstance(txid, str)
        return self.mempool[txid]


def _txid_of(tx_hex: str) -> str:
    """Return the txid a serialized tx's own hex answers to on the wire.

    `_FakeRpc.call` uses this so that its default `sendrawtransaction`
    answer is always right, without hard-coding one -- `Tx.parse` and
    `.id` are btclib's own, the same computation `send_self_transfer`
    checks its answer against.
    """
    from io import BytesIO  # noqa: PLC0415

    from btclib.tx import Tx  # noqa: PLC0415

    return Tx.parse(BytesIO(bytes.fromhex(tx_hex))).id.hex()


class _FakeNode(NodeAdapter):
    """A `NodeAdapter` answering `.rpc` with a `_FakeRpc`, never spawned.

    `node_test.py`'s own `_FakeAdapter`: `MiniWallet` reads nothing off a
    node but `.rpc`, so `_command` and `_rpc_client` below are never
    called by anything this module's own tests exercise.
    """

    capabilities: AbstractSet[Capability] = frozenset()

    def __init__(self, rpc: _FakeRpc) -> None:
        self._fake_rpc = rpc

    @override
    def _command(self) -> list[str]:
        return []

    @override
    def _rpc_client(self) -> _FakeRpc:  # type: ignore[override]
        return self._fake_rpc


def test_init_reads_the_current_tip_and_height() -> None:
    """The constructor asks for nothing but where the chain already is."""
    rpc = _FakeRpc(best_hash="ab" * 32, height=7)
    wallet = MiniWallet(_FakeNode(rpc))
    assert wallet.get_balance() == 0
    assert wallet.script_pub_key.address == _ADDRESS_OP_TRUE


def test_fake_node_is_never_actually_started() -> None:
    """`_command` exists to satisfy `NodeAdapter`, and answers nothing.

    `MiniWallet` never spawns a process, so nothing here ever calls
    `NodeAdapter.start`, the one caller `_command` otherwise has -- this
    is the whole of what covers it.
    """
    node = _FakeNode(_FakeRpc())
    assert node._command() == []


@pytest.mark.parametrize("best_hash", [123, None])
def test_init_refuses_a_non_string_best_hash(best_hash: object) -> None:
    """`getbestblockhash` answering anything but a string is refused."""
    rpc = _FakeRpc()
    with (
        patch.object(_FakeRpc, "call", return_value=best_hash),
        pytest.raises(TypeError, match="getbestblockhash"),
    ):
        MiniWallet(_FakeNode(rpc))


def test_init_refuses_a_non_int_height() -> None:
    """`getblockcount` answering anything but an int is refused."""
    rpc = _FakeRpc()
    calls = {"n": 0}

    def _call(
        _self: _FakeRpc, method: str, params: list[object] | None = None
    ) -> object:
        del params
        calls["n"] += 1
        if method == "getbestblockhash":
            return _GENESIS
        assert method == "getblockcount"
        return "not-an-int"

    with (
        patch.object(_FakeRpc, "call", _call),
        pytest.raises(TypeError, match="getblockcount"),
    ):
        MiniWallet(_FakeNode(rpc))


@pytest.mark.parametrize("chain_info", [123, None, {}, {"mediantime": "x"}])
def test_init_refuses_a_bad_getblockchaininfo_answer(chain_info: object) -> None:
    """`getblockchaininfo` answering no int `mediantime` is refused."""
    rpc = _FakeRpc()

    def _call(
        _self: _FakeRpc, method: str, params: list[object] | None = None
    ) -> object:
        del params
        if method == "getbestblockhash":
            return _GENESIS
        if method == "getblockcount":
            return 0
        assert method == "getblockchaininfo"
        return chain_info

    with (
        patch.object(_FakeRpc, "call", _call),
        pytest.raises(TypeError, match="getblockchaininfo"),
    ):
        MiniWallet(_FakeNode(rpc))


def test_generate_mines_and_caches_one_coin_per_block() -> None:
    """`generate` builds, mines and submits, with no RPC scan afterwards."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    hashes = wallet.generate(3)
    assert len(hashes) == 3
    assert len(rpc.submitted) == 3
    assert wallet.get_balance() > 0


def test_generate_halves_the_subsidy_at_regtest_s_own_interval() -> None:
    """Regtest halves at height 150, not at mainnet's 210000.

    A coinbase claiming mainnet's subsidy past that height is refused by
    bitcoind as `bad-cb-amount`; both the coinbase each block carries and
    the value this wallet caches for it must halve there.
    """
    rpc = _FakeRpc(height=148)
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(2)
    paid = [
        Block.parse(bytes.fromhex(block_hex), check_validity=False)
        .transactions[0]
        .vout[0]
        .value
        for block_hex in rpc.submitted
    ]
    assert paid == [5_000_000_000, 2_500_000_000]
    assert wallet.get_balance() == sum(paid)


def test_generate_seeds_the_first_block_time_from_the_chains_mediantime() -> None:
    """A wallet built on a pre-existing chain does not mine `time-too-old`.

    The session-scoped `bitcoind_adapter` fixture hands more than one
    `MiniWallet` the same node: a chain another wallet already mined many
    blocks into rapidly leaves a median-time-past ahead of the wall clock,
    which this wallet's own first block still has to exceed.
    """
    far_future_median_time = int(datetime.now(UTC).timestamp()) + 10_000
    rpc = _FakeRpc(median_time=far_future_median_time)
    wallet = MiniWallet(_FakeNode(rpc))

    wallet.generate(1)

    block = Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
    assert int(block.header.time.timestamp()) > far_future_median_time


def test_generate_refuses_a_submitblock_rejection() -> None:
    """A `submitblock` answer other than acceptance is not swallowed."""
    rpc = _FakeRpc(submit_answer="bad-block-header")
    wallet = MiniWallet(_FakeNode(rpc))
    with pytest.raises(TypeError, match="submitblock refused"):
        wallet.generate(1)


def test_generate_refuses_an_unsolved_candidate() -> None:
    """A `mine` search that comes back empty is reported, not retried."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    with (
        patch("btclib_functional_tests.mini_wallet.mine", return_value=None),
        pytest.raises(RuntimeError, match="no nonce solved"),
    ):
        wallet.generate(1)


def test_create_self_transfer_refuses_with_no_matured_coin() -> None:
    """A coin mined this call is not yet `COINBASE_MATURITY` deep."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)
    with pytest.raises(LookupError, match="no coin"):
        wallet.create_self_transfer()


def test_create_self_transfer_spends_a_matured_coin() -> None:
    """A coin `COINBASE_MATURITY` blocks deep is spendable."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    balance_before = wallet.get_balance()

    tx = wallet.create_self_transfer()

    assert len(tx.vin) == 1
    assert len(tx.vout) == 1
    assert tx.vout[0].script_pub_key == wallet.script_pub_key
    # the spent coin left the cache, and the new one did not join it
    assert wallet.get_balance() == balance_before - tx.vout[0].value - _DEFAULT_FEE


def test_create_self_transfer_can_spend_a_coin_it_already_sent() -> None:
    """A non-coinbase coin this class itself minted needs no maturity wait.

    Exactly `COINBASE_MATURITY` blocks, not one more: mining a second
    matured coinbase alongside the first would let the second call below
    pop *that* one instead, `get_utxo`'s own largest-first order putting
    it ahead of the coin `send_self_transfer` just minted.
    """
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY)

    first = wallet.send_self_transfer()
    second = wallet.create_self_transfer()

    assert second.vin[0].prev_out.tx_id == first.id


def test_send_self_transfer_broadcasts_and_returns_the_sent_tx() -> None:
    """The tx sent is the one `create_self_transfer` built, byte for byte."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = wallet.send_self_transfer()

    assert len(rpc.sent) == 1
    assert rpc.sent[0] == tx.serialize(True, check_validity=False).hex()


def test_send_self_transfer_refuses_a_mismatched_answer() -> None:
    """An answer other than the sent tx's own id is refused, not trusted."""
    rpc = _FakeRpc(send_answer="not-the-txid")
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    with pytest.raises(TypeError, match="sendrawtransaction answered"):
        wallet.send_self_transfer()


def test_generate_confirms_a_named_tx_only_in_the_first_block() -> None:
    """`confirm` rides in the first of several blocks, not every one."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.send_self_transfer()

    wallet.generate(2, confirm=[tx])

    first_block = Block.parse(bytes.fromhex(rpc.submitted[-2]), check_validity=False)
    second_block = Block.parse(bytes.fromhex(rpc.submitted[-1]), check_validity=False)
    assert tx.id in {carried.id for carried in first_block.transactions}
    assert tx.id not in {carried.id for carried in second_block.transactions}


def test_generate_confirms_nothing_by_default() -> None:
    """A block `generate` mines with no `confirm` carries only its coinbase."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))

    wallet.generate(1)

    block = Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
    assert len(block.transactions) == 1


def test_get_utxo_selects_the_named_coin_not_merely_the_oldest() -> None:
    """`get_utxo` returns the coin `txid` names, not whichever is first."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(2)
    second_block = Block.parse(bytes.fromhex(rpc.submitted[1]), check_validity=False)
    txid = second_block.transactions[0].id.hex()

    utxo = wallet.get_utxo(txid=txid)

    assert utxo.outpoint.tx_id.hex() == txid
    assert utxo.height == 2
    # the first coin is still cached: only the named one was popped
    assert wallet.get_balance() > 0


def test_get_utxo_ignores_maturity() -> None:
    """A coin one block deep is still returned when named by its own txid."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)
    block = Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)

    utxo = wallet.get_utxo(txid=block.transactions[0].id.hex())

    assert utxo.coinbase is True


def test_get_utxo_pops_the_coin_it_returns() -> None:
    """A second call for the same txid finds nothing left to return."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)
    txid = (
        Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
        .transactions[0]
        .id.hex()
    )
    wallet.get_utxo(txid=txid)
    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid=txid)


def test_get_utxo_refuses_an_unknown_txid() -> None:
    """A txid this wallet never cached is refused, not confused for one."""
    wallet = MiniWallet(_FakeNode(_FakeRpc()))
    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid="00" * 32)


def test_create_self_transfer_spends_the_named_utxo_regardless_of_maturity() -> None:
    """A caller-named coin bypasses the automatic pick's own maturity check."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)  # far short of COINBASE_MATURITY
    coin = wallet.get_utxo(
        txid=Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
        .transactions[0]
        .id.hex()
    )

    tx = wallet.create_self_transfer(utxo_to_spend=coin)

    assert tx.vin[0].prev_out == coin.outpoint


def test_send_self_transfer_spends_the_named_utxo() -> None:
    """`utxo_to_spend` reaches `create_self_transfer` through this call too."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)
    coin = wallet.get_utxo(
        txid=Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
        .transactions[0]
        .id.hex()
    )

    tx = wallet.send_self_transfer(utxo_to_spend=coin)

    assert tx.vin[0].prev_out == coin.outpoint
    assert rpc.sent[0] == tx.serialize(True, check_validity=False).hex()


def test_resync_picks_up_a_tip_this_wallet_did_not_mine() -> None:
    """`generate` after `resync` extends the node's own tip, not a stale one."""
    rpc = _FakeRpc(best_hash="11" * 32, height=5, median_time=1_000)
    wallet = MiniWallet(_FakeNode(rpc))
    # the chain moved by some other means -- an invalidateblock, a
    # build_fork submission -- this wallet never saw
    rpc._best_hash = "22" * 32
    rpc._height = 9
    rpc._median_time = 2_000

    wallet.resync()
    wallet.generate(1)

    block = Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
    assert block.header.previous_block_hash == bytes.fromhex("22" * 32)


def test_resync_leaves_the_coin_cache_untouched() -> None:
    """`resync` adds and drops no coin: it has no `scantxoutset` to use."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)
    balance_before = wallet.get_balance()

    wallet.resync()

    assert wallet.get_balance() == balance_before


def test_build_fork_extends_the_nodes_own_current_tip() -> None:
    """The first fork block's own parent is the node's tip, not a fixed one."""
    rpc = _FakeRpc(best_hash="33" * 32, height=4, median_time=500)
    node = _FakeNode(rpc)
    wallet = MiniWallet(node)

    fork = build_fork(node, wallet.script_pub_key, 3)

    assert len(fork) == 3
    assert fork[0].header.previous_block_hash == bytes.fromhex("33" * 32)
    assert not rpc.submitted  # nothing is submitted on the caller's behalf


def test_build_fork_chains_each_block_to_the_previous_one() -> None:
    """Blocks 2..N of a fork extend the fork itself, not the node's own tip."""
    rpc = _FakeRpc()
    node = _FakeNode(rpc)
    wallet = MiniWallet(node)

    fork = build_fork(node, wallet.script_pub_key, 3)

    for index in range(1, len(fork)):
        assert fork[index].header.previous_block_hash == fork[index - 1].header.hash


def test_build_fork_pays_the_given_script_pub_key() -> None:
    """Every block's own coinbase pays what the caller asked for."""
    rpc = _FakeRpc()
    node = _FakeNode(rpc)
    wallet = MiniWallet(node)

    fork = build_fork(node, wallet.script_pub_key, 1)

    assert fork[0].transactions[0].vout[0].script_pub_key == wallet.script_pub_key


def test_build_next_block_extends_the_nodes_own_current_tip() -> None:
    """The block's parent is the node's tip, its coinbase the next height's."""
    rpc = _FakeRpc(best_hash="44" * 32, height=6, median_time=500)

    block = build_next_block(_FakeNode(rpc), RAW_P2PK_SCRIPT_PUB_KEY)

    assert block.header.previous_block_hash == bytes.fromhex("44" * 32)
    coinbase = block.transactions[0]
    assert coinbase.vin[0].script_sig.startswith(bip34_commitment(7))
    assert [tx_out.script_pub_key for tx_out in coinbase.vout] == [
        RAW_P2PK_SCRIPT_PUB_KEY
    ]
    assert block.header.version == VERSION
    assert not rpc.submitted  # nothing is submitted on the caller's behalf


def test_build_next_block_carries_the_transactions_in_order() -> None:
    """What the caller names follows the coinbase, in the order given."""
    rpc = _FakeRpc()
    spends = [
        Tx(
            version=2,
            lock_time=0,
            vin=[TxIn(OutPoint(bytes([index]) * 32, 0), script_sig=b"\x51")],
            vout=[TxOut(1_000, RAW_P2PK_SCRIPT_PUB_KEY)],
        )
        for index in (2, 1)
    ]

    block = build_next_block(_FakeNode(rpc), RAW_P2PK_SCRIPT_PUB_KEY, spends)

    assert block.transactions[1:] == spends


def test_build_next_block_takes_the_version_and_an_extra_output() -> None:
    """The header's own version, and a second, zero-valued coinbase output."""
    rpc = _FakeRpc()
    extra = nulldata_script_pub_key(b"\x01")

    block = build_next_block(
        _FakeNode(rpc), RAW_P2PK_SCRIPT_PUB_KEY, version=3, extra_output_script=extra
    )

    assert block.header.version == 3
    second = block.transactions[0].vout[1]
    assert (second.value, second.script_pub_key) == (0, extra)


def test_build_next_block_is_timed_past_the_chains_mediantime() -> None:
    """A median-time-past ahead of the wall clock is exceeded by one second."""
    far_future_median_time = int(datetime.now(UTC).timestamp()) + 10_000
    rpc = _FakeRpc(median_time=far_future_median_time)

    block = build_next_block(_FakeNode(rpc), RAW_P2PK_SCRIPT_PUB_KEY)

    assert int(block.header.time.timestamp()) == far_future_median_time + 1


def test_build_next_block_takes_the_time_as_given() -> None:
    """A caller's own time is the header's, even behind the median-time-past."""
    median_time = int(datetime.now(UTC).timestamp())
    rpc = _FakeRpc(median_time=median_time)

    block = build_next_block(
        _FakeNode(rpc), RAW_P2PK_SCRIPT_PUB_KEY, time=median_time - 600
    )

    assert int(block.header.time.timestamp()) == median_time - 600


@pytest.mark.parametrize("size", [0, 1, 80, 81, 256])
def test_nulldata_script_pub_key_accepts_any_length(size: int) -> None:
    """Unlike `ScriptPubKey.nulldata`, no length here is refused."""
    data = b"\xff" * size
    script = nulldata_script_pub_key(data)
    assert script.script.startswith(b"\x6a")  # OP_RETURN
    assert data in script.script


def test_raw_p2pk_script_pub_key_pays_the_generator() -> None:
    """Core's own `RAW_P2PK` output: private key 1, so the generator's SEC."""
    g_x, g_y = secp256k1.G
    sec = bytes([2 + g_y % 2]) + g_x.to_bytes(32, "big")
    assert RAW_P2PK_SCRIPT_PUB_KEY.script == script_serialize([sec, "OP_CHECKSIG"])


def test_raw_p2pk_script_sig_signs_the_legacy_sighash() -> None:
    """One push: a signature the generator verifies, `SIGHASH_ALL` appended."""
    tx_in = TxIn(OutPoint(b"\x01" * 32, 0))
    tx = Tx(
        version=2, lock_time=0, vin=[tx_in], vout=[TxOut(1, RAW_P2PK_SCRIPT_PUB_KEY)]
    )
    (pushed_hex,) = parse(raw_p2pk_script_sig(tx, 0))
    assert isinstance(pushed_hex, str)  # `parse` answers a push as hex
    pushed = bytes.fromhex(pushed_hex)
    assert pushed[-1] == sig_hash.ALL
    digest = sig_hash.legacy(RAW_P2PK_SCRIPT_PUB_KEY.script, tx, 0, sig_hash.ALL)
    assert verify_(digest, secp256k1.G, pushed[:-1])


def test_send_to_pays_the_named_script_and_returns_change() -> None:
    """The second output pays the caller's script; the first is change."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    balance_before = wallet.get_balance()
    target = nulldata_script_pub_key(b"target")

    tx = wallet.send_to(target, 12345)

    assert len(tx.vin) == 1
    assert len(tx.vout) == 2
    assert tx.vout[0].script_pub_key == wallet.script_pub_key
    assert tx.vout[1].script_pub_key == target
    assert tx.vout[1].value == 12345
    assert rpc.sent == [tx.serialize(True, check_validity=False).hex()]
    # the change is cached back, so only `value` and `FEE` left the wallet
    assert wallet.get_balance() == balance_before - 12345 - FEE


def test_send_to_refuses_with_no_matured_coin() -> None:
    """A coin mined this call is not yet `COINBASE_MATURITY` deep."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)
    with pytest.raises(LookupError, match="no coin"):
        wallet.send_to(nulldata_script_pub_key(b""), 1)


def test_send_to_refuses_a_value_the_coin_cannot_cover() -> None:
    """`value` plus `FEE` beyond the spent coin's own value is refused."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    with pytest.raises(ValueError, match="cannot cover"):
        wallet.send_to(nulldata_script_pub_key(b""), 5_000_000_000)


def test_send_to_refuses_a_mismatched_answer() -> None:
    """An answer other than the sent tx's own id is refused, not trusted."""
    rpc = _FakeRpc(send_answer="not-the-txid")
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    with pytest.raises(TypeError, match="sendrawtransaction answered"):
        wallet.send_to(nulldata_script_pub_key(b""), 1)


def test_get_utxo_selects_the_named_vout_among_several() -> None:
    """`vout` disambiguates several cached coins sharing one `txid`."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.send_self_transfer_multi(num_outputs=2)

    second = wallet.get_utxo(txid=tx.id.hex(), vout=1)
    first = wallet.get_utxo(txid=tx.id.hex(), vout=0)

    assert second.outpoint.vout == 1
    assert first.outpoint.vout == 0


def test_get_utxo_refuses_an_unknown_vout() -> None:
    """A `txid` this wallet knows but the wrong `vout` is still refused."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.send_self_transfer()
    with pytest.raises(LookupError, match="vout 1"):
        wallet.get_utxo(txid=tx.id.hex(), vout=1)


def test_create_self_transfer_pads_to_the_target_vsize() -> None:
    """`target_vsize` is met exactly, an `OP_RETURN` output padding the rest."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = wallet.create_self_transfer(target_vsize=250)

    assert tx.vsize == 250
    assert len(tx.vout) == 2
    assert tx.vout[1].script_pub_key.script.startswith(b"\x6a")  # OP_RETURN


def test_padding_datacarrier_size_admits_the_padding_of_a_standard_tx() -> None:
    """The padding of a 100000-vbyte transaction fits the option."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = wallet.create_self_transfer(target_vsize=100_000)

    assert tx.vsize == 100_000
    assert len(tx.vout[1].script_pub_key.script) <= PADDING_DATACARRIER_SIZE


def test_create_self_transfer_refuses_a_target_vsize_too_small() -> None:
    """A `target_vsize` below the tx's own size before padding is refused."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    with pytest.raises(ValueError, match="smaller than"):
        wallet.create_self_transfer(target_vsize=1)


def test_create_self_transfer_multi_spends_one_coin_into_several() -> None:
    """Every output is `fee_per_output` short of an equal share, and equal."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    coin_value = (
        Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
        .transactions[0]
        .vout[0]
        .value
    )

    tx = wallet.create_self_transfer_multi(num_outputs=3, fee_per_output=100)

    assert len(tx.vin) == 1
    assert len(tx.vout) == 3
    expected_per_output = (coin_value - 100 * 3) // 3
    assert all(out.value == expected_per_output for out in tx.vout)


def test_create_self_transfer_multi_spends_several_coins_into_one() -> None:
    """Every named coin funds the single output, less one `fee_per_output`."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 2)
    coins = [
        wallet.get_utxo(
            txid=Block.parse(bytes.fromhex(block_hex), check_validity=False)
            .transactions[0]
            .id.hex()
        )
        for block_hex in rpc.submitted[:2]
    ]
    total = sum(coin.value for coin in coins)

    tx = wallet.create_self_transfer_multi(utxos_to_spend=coins, fee_per_output=100)

    assert len(tx.vin) == 2
    assert len(tx.vout) == 1
    assert tx.vout[0].value == total - 100


def test_create_self_transfer_multi_refuses_a_fee_that_exhausts_the_output() -> None:
    """A `fee_per_output` at or beyond the inputs' own total is refused."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    coin_value = wallet.get_balance()
    with pytest.raises(ValueError, match="does not cover"):
        wallet.create_self_transfer_multi(fee_per_output=coin_value)


def test_create_self_transfer_multi_pads_to_the_target_vsize() -> None:
    """`target_vsize` still adds one padding output beyond `num_outputs`."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = wallet.create_self_transfer_multi(num_outputs=2, target_vsize=400)

    assert tx.vsize == 400
    assert len(tx.vout) == 3


def test_send_self_transfer_multi_broadcasts_and_returns_the_sent_tx() -> None:
    """The tx sent is `create_self_transfer_multi`'s own, byte for byte."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = wallet.send_self_transfer_multi(num_outputs=2)

    assert len(rpc.sent) == 1
    assert rpc.sent[0] == tx.serialize(True, check_validity=False).hex()


def test_send_self_transfer_multi_refuses_a_mismatched_answer() -> None:
    """An answer other than the sent tx's own id is refused, not trusted."""
    rpc = _FakeRpc(send_answer="not-the-txid")
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    with pytest.raises(TypeError, match="sendrawtransaction answered"):
        wallet.send_self_transfer_multi()


def test_create_self_transfer_chain_links_each_tx_to_the_last() -> None:
    """The nth tx of the chain spends the (n-1)th's own single output."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    chain = wallet.create_self_transfer_chain(chain_length=3)

    assert len(chain) == 3
    for parent, child in pairwise(chain):
        assert child.vin[0].prev_out == OutPoint(parent.id, 0)


def test_send_self_transfer_chain_leaves_only_its_own_tip_cached() -> None:
    """The chain's last output is left for a caller to spend further.

    Every other output the chain sends along the way is spent by the next
    transaction, and that transaction's own broadcast drops it again.
    """
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    chain = wallet.send_self_transfer_chain(chain_length=3)
    tip = wallet.get_utxo(txid=chain[-1].id.hex(), vout=0)

    assert tip.outpoint == OutPoint(chain[-1].id, 0)
    for tx in chain[:-1]:
        with pytest.raises(LookupError, match="no coin"):
            wallet.get_utxo(txid=tx.id.hex())


def test_create_self_transfer_chain_caches_nothing() -> None:
    """Each link spends `new_utxos`' own answer, not a cached coin."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    balance_before = wallet.get_balance()

    chain = wallet.create_self_transfer_chain(chain_length=3)

    spent = chain[0].vout[0].value + _DEFAULT_FEE
    assert wallet.get_balance() == balance_before - spent
    for tx in chain:
        with pytest.raises(LookupError, match="no coin"):
            wallet.get_utxo(txid=tx.id.hex())


def test_create_self_transfer_chain_starts_from_the_named_utxo() -> None:
    """`utxo_to_spend` reaches the chain's first transaction, not `None`."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(1)
    coin = wallet.get_utxo(
        txid=Block.parse(bytes.fromhex(rpc.submitted[0]), check_validity=False)
        .transactions[0]
        .id.hex()
    )

    chain = wallet.create_self_transfer_chain(chain_length=1, utxo_to_spend=coin)

    assert chain[0].vin[0].prev_out == coin.outpoint


def test_send_self_transfer_chain_broadcasts_every_tx() -> None:
    """Every transaction of the chain is sent, in chain order."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    chain = wallet.send_self_transfer_chain(chain_length=2)

    assert rpc.sent == [tx.serialize(True, check_validity=False).hex() for tx in chain]


def test_send_self_transfer_chain_refuses_a_mismatched_answer() -> None:
    """An answer other than the sent tx's own id is refused, not trusted."""
    rpc = _FakeRpc(send_answer="not-the-txid")
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    with pytest.raises(TypeError, match="sendrawtransaction answered"):
        wallet.send_self_transfer_chain(chain_length=2)


def _coinbase_txid(rpc: _FakeRpc, index: int) -> str:
    """Return the txid of the coinbase of the `index`th block submitted."""
    block = Block.parse(bytes.fromhex(rpc.submitted[index]), check_validity=False)
    return block.transactions[0].id.hex()


def test_utxo_confirmed_reads_its_own_height() -> None:
    """A coin at height `0` is one no block holds; any other height is."""
    outpoint = OutPoint(b"\x01" * 32, 0)
    assert Utxo(outpoint, 1, 5, coinbase=False).confirmed
    assert not Utxo(outpoint, 1, 0, coinbase=False).confirmed


def test_generate_caches_each_coinbase_at_its_own_height() -> None:
    """A coin `generate` mines is confirmed from the moment it is cached."""
    rpc = _FakeRpc(height=4)
    wallet = MiniWallet(_FakeNode(rpc))

    wallet.generate(2)

    utxos = wallet.get_utxos(include_immature_coinbase=True)
    assert sorted(utxo.height for utxo in utxos) == [5, 6]
    assert all(utxo.confirmed for utxo in utxos)


def test_a_self_transfer_is_cached_unconfirmed() -> None:
    """A broadcast no block of this wallet's has carried is not confirmed."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = wallet.send_self_transfer()

    assert not wallet.get_utxo(txid=tx.id.hex()).confirmed


def test_generate_confirms_every_output_its_own_confirm_carries() -> None:
    """Each coin a `confirm` transaction pays takes the first block's height."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.send_self_transfer_multi(num_outputs=2)

    wallet.generate(2, confirm=[tx])

    coins = [wallet.get_utxo(txid=tx.id.hex(), vout=vout) for vout in (0, 1)]
    assert [coin.height for coin in coins] == [COINBASE_MATURITY + 2] * 2


def test_get_utxo_confirmed_only_skips_a_coin_no_block_holds() -> None:
    """Of a confirmed coin and a mempool-only one, only the first answers."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY)  # one coinbase matured, no more
    tx = wallet.send_self_transfer()

    assert wallet.get_utxo(mark_as_spent=False).outpoint == OutPoint(tx.id, 0)
    with pytest.raises(LookupError, match="matured yet, confirmed"):
        wallet.get_utxo(confirmed_only=True)
    with pytest.raises(LookupError, match="confirmed"):
        wallet.get_utxo(txid=tx.id.hex(), confirmed_only=True)

    wallet.generate(1, confirm=[tx])

    coin = wallet.get_utxo(txid=tx.id.hex(), confirmed_only=True)
    assert coin.height == COINBASE_MATURITY + 1


def test_get_utxo_takes_the_largest_matured_coin_not_the_oldest() -> None:
    """Without `txid`, the largest matured coin, as `wallet.py` sorts."""
    rpc = _FakeRpc(height=148)
    wallet = MiniWallet(_FakeNode(rpc))
    # height 149 pays 50 BTC, 150 on pay 25 BTC: regtest halves at 150
    wallet.generate(COINBASE_MATURITY + 2)
    first = wallet.send_self_transfer()
    assert first.vin[0].prev_out.tx_id.hex() == _coinbase_txid(rpc, 0)

    # cached last, and 50 BTC less its fee outweighs the 25 BTC coinbases
    # still ahead of it in the order they were cached
    assert wallet.get_utxo().outpoint == OutPoint(first.id, 0)


def test_get_utxo_sorts_the_cache_by_value_then_descending_height() -> None:
    """The order `get_utxos` returns after a `get_utxo` is `wallet.py`'s."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 2)
    tx_a = wallet.send_self_transfer_multi(num_outputs=2)
    wallet.generate(1, confirm=[tx_a])
    tx_b = wallet.send_self_transfer_multi(num_outputs=2)

    largest = wallet.get_utxo(mark_as_spent=False)
    utxos = wallet.get_utxos(include_immature_coinbase=True, mark_as_spent=False)

    # equal values: the confirmed pair ahead of the one at height 0
    assert [utxo.outpoint for utxo in utxos[:4]] == [
        OutPoint(tx_a.id, 0),
        OutPoint(tx_a.id, 1),
        OutPoint(tx_b.id, 0),
        OutPoint(tx_b.id, 1),
    ]
    heights = [utxo.height for utxo in utxos[4:]]
    assert heights == sorted(heights, reverse=True)
    assert utxos[-1] == largest


def test_a_coin_taken_without_being_marked_spent_is_dropped_once_spent() -> None:
    """`mark_as_spent=False` keeps it cached until a sent tx spends it.

    Building the spend is not sending it, `scan_tx` running in Core's own
    `sendrawtransaction` (`wallet.py`), so the coin survives the
    `create_self_transfer` and goes only with the `send_self_transfer`.
    """
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    balance = wallet.get_balance()

    coin = wallet.get_utxo(mark_as_spent=False)
    assert wallet.get_balance() == balance

    wallet.create_self_transfer(utxo_to_spend=coin)
    assert wallet.get_balance() == balance
    assert wallet.get_utxo(txid=coin.outpoint.tx_id.hex(), mark_as_spent=False) == coin

    wallet.send_self_transfer(utxo_to_spend=coin)
    assert wallet.get_balance() == balance - _DEFAULT_FEE
    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid=coin.outpoint.tx_id.hex())


def test_get_utxos_leaves_out_an_immature_coinbase_by_default() -> None:
    """`include_immature_coinbase` is what brings them back."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)  # two coinbases matured

    matured = wallet.get_utxos(mark_as_spent=False)
    every = wallet.get_utxos(include_immature_coinbase=True, mark_as_spent=False)

    assert [utxo.height for utxo in matured] == [1, 2]
    assert len(every) == COINBASE_MATURITY + 1


def test_get_utxos_confirmed_only_leaves_out_a_coin_no_block_holds() -> None:
    """A mempool-only coin is returned without `confirmed_only`, not with it."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY)
    tx = wallet.send_self_transfer()

    unfiltered = wallet.get_utxos(mark_as_spent=False)
    confirmed = wallet.get_utxos(confirmed_only=True, mark_as_spent=False)

    assert [utxo.outpoint for utxo in unfiltered] == [OutPoint(tx.id, 0)]
    assert confirmed == []


def test_get_utxos_marking_spent_forgets_the_whole_cache() -> None:
    """Immature coins the filter left out are forgotten too, as in Core."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    returned = wallet.get_utxos()

    assert len(returned) == 2
    assert wallet.get_balance() == 0


def test_resync_confirms_a_coin_a_block_mined_elsewhere_holds() -> None:
    """`gettxout`'s own confirmations become the coin's height."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY)
    tx = wallet.send_self_transfer()
    # the node mined a block of its own around tx: one block deep
    rpc._height = COINBASE_MATURITY + 1
    rpc.txouts[tx.id.hex(), 0] = {"confirmations": 1}

    wallet.resync()

    coin = wallet.get_utxo(txid=tx.id.hex(), confirmed_only=True)
    assert coin.height == COINBASE_MATURITY + 1
    # a coinbase is never asked about: `generate` already knows its height
    assert rpc.asked == [(tx.id.hex(), 0)]


def test_resync_unconfirms_a_coin_the_utxo_set_no_longer_holds() -> None:
    """A confirmed coin a reorg sent back to the mempool reads as height 0."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY)
    tx = wallet.send_self_transfer()
    wallet.generate(1, confirm=[tx])

    wallet.resync()  # gettxout answers None for every pair the fake lacks

    assert not wallet.get_utxo(txid=tx.id.hex()).confirmed


@pytest.mark.parametrize(
    "answer", [123, {}, {"confirmations": "1"}, {"confirmations": 0}]
)
def test_resync_refuses_a_bad_gettxout_answer(answer: object) -> None:
    """A `gettxout` answer with no positive int confirmations is refused."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY)
    tx = wallet.send_self_transfer()
    rpc.txouts[tx.id.hex(), 0] = answer

    with pytest.raises(TypeError, match="gettxout"):
        wallet.resync()


@pytest.mark.parametrize(
    "spend",
    [
        lambda wallet: wallet.create_self_transfer(confirmed_only=True),
        lambda wallet: wallet.send_self_transfer(confirmed_only=True),
        lambda wallet: wallet.create_self_transfer_multi(confirmed_only=True),
        lambda wallet: wallet.send_self_transfer_multi(confirmed_only=True),
    ],
    ids=[
        "create_self_transfer",
        "send_self_transfer",
        "create_self_transfer_multi",
        "send_self_transfer_multi",
    ],
)
def test_confirmed_only_reaches_get_utxo_from_every_spend(
    spend: Callable[[MiniWallet], object],
) -> None:
    """With only a mempool-only coin matured, each spend finds nothing."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY)
    wallet.send_self_transfer()

    with pytest.raises(LookupError, match="confirmed"):
        spend(wallet)


def _funded_wallet() -> tuple[_FakeRpc, MiniWallet, Utxo]:
    """Return a wallet holding one matured coin, and that coin, still cached."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    return rpc, wallet, wallet.get_utxo(mark_as_spent=False)


def test_create_self_transfer_defaults_are_core_s_own() -> None:
    """Core's defaults: 0.003 BTC/kvB, version 2, `nLockTime`, `nSequence` 0."""
    _, wallet, coin = _funded_wallet()

    tx = wallet.create_self_transfer(utxo_to_spend=coin)

    assert DEFAULT_FEE_RATE * _SELF_TRANSFER_VSIZE // 1000 == _DEFAULT_FEE
    assert tx.vsize == _SELF_TRANSFER_VSIZE
    assert coin.value - tx.vout[0].value == _DEFAULT_FEE
    assert tx.version == 2
    assert tx.lock_time == 0
    assert tx.vin[0].sequence == 0


@pytest.mark.parametrize(
    "fee_rate,fee",
    [
        (0, 0),
        (1000, 104),
        # 1283.88 sat: rounded up, never down, as `CFeeRate::GetFee` does
        (12_345, 1284),
    ],
)
def test_create_self_transfer_prices_fee_rate_over_its_own_vsize(
    fee_rate: int, fee: int
) -> None:
    """`fee_rate` is satoshis per 1000 virtual bytes, rounded up."""
    _, wallet, coin = _funded_wallet()

    tx = wallet.create_self_transfer(utxo_to_spend=coin, fee_rate=fee_rate)

    assert coin.value - tx.vout[0].value == fee


def test_create_self_transfer_takes_fee_over_fee_rate() -> None:
    """A nonzero `fee` is the fee, whatever `fee_rate` says."""
    _, wallet, coin = _funded_wallet()

    tx = wallet.create_self_transfer(utxo_to_spend=coin, fee=5000, fee_rate=1)

    assert coin.value - tx.vout[0].value == 5000


def test_create_self_transfer_prices_fee_rate_over_target_vsize() -> None:
    """With `target_vsize` and no `fee`, the padded size is what is priced."""
    _, wallet, coin = _funded_wallet()

    tx = wallet.create_self_transfer(
        utxo_to_spend=coin, fee_rate=12_345, target_vsize=250
    )

    assert tx.vsize == 250
    # 3086.25 sat, rounded up; the padding output itself carries nothing
    assert coin.value - sum(out.value for out in tx.vout) == 3087
    assert tx.vout[1].value == 0


def test_create_self_transfer_keeps_fee_with_target_vsize() -> None:
    """With `target_vsize` and a `fee`, the fee is still exactly `fee`."""
    _, wallet, coin = _funded_wallet()

    tx = wallet.create_self_transfer(utxo_to_spend=coin, fee=777, target_vsize=250)

    assert tx.vsize == 250
    assert coin.value - sum(out.value for out in tx.vout) == 777


@pytest.mark.parametrize("fee_rate,fee", [(-1, 0), (0, -1)])
def test_create_self_transfer_refuses_a_negative_fee_before_taking_a_coin(
    fee_rate: int, fee: int
) -> None:
    """A negative `fee_rate` or `fee` is refused, no coin leaving the cache."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    balance_before = wallet.get_balance()

    with pytest.raises(ValueError, match="must not be negative"):
        wallet.create_self_transfer(fee_rate=fee_rate, fee=fee)

    assert wallet.get_balance() == balance_before


def test_create_self_transfer_refuses_a_fee_the_coin_cannot_cover() -> None:
    """A fee at or beyond the coin's own value leaves nothing to send."""
    _, wallet, coin = _funded_wallet()
    with pytest.raises(ValueError, match="cannot cover"):
        wallet.create_self_transfer(utxo_to_spend=coin, fee=coin.value)


def test_create_self_transfer_sets_version_locktime_and_sequence() -> None:
    """`version`, `locktime` and `sequence` are the tx's own fields."""
    _, wallet, coin = _funded_wallet()

    tx = wallet.create_self_transfer(
        utxo_to_spend=coin, version=3, locktime=500, sequence=0xFFFFFFFD
    )

    assert tx.version == 3
    assert tx.lock_time == 500
    assert tx.vin[0].sequence == 0xFFFFFFFD


def test_create_self_transfer_takes_a_one_element_sequence() -> None:
    """A list of one `nSequence` is taken the way a bare one is."""
    _, wallet, coin = _funded_wallet()

    tx = wallet.create_self_transfer(utxo_to_spend=coin, sequence=[7])

    assert tx.vin[0].sequence == 7


def test_create_self_transfer_refuses_more_sequences_than_inputs() -> None:
    """More `nSequence` values than inputs is refused, not truncated."""
    _, wallet, coin = _funded_wallet()
    with pytest.raises(ValueError, match="2 sequence"):
        wallet.create_self_transfer(utxo_to_spend=coin, sequence=[1, 2])


def test_send_self_transfer_forwards_every_parameter() -> None:
    """What `send_self_transfer` sends is `create_self_transfer`'s answer."""
    rpc, wallet, coin = _funded_wallet()

    tx = wallet.send_self_transfer(
        utxo_to_spend=coin,
        fee_rate=1000,
        target_vsize=300,
        version=3,
        locktime=9,
        sequence=5,
    )

    assert rpc.sent == [tx.serialize(True, check_validity=False).hex()]
    assert tx.vsize == 300
    assert coin.value - sum(out.value for out in tx.vout) == 300
    assert (tx.version, tx.lock_time, tx.vin[0].sequence) == (3, 9, 5)


def test_send_self_transfer_forwards_fee_and_confirmed_only() -> None:
    """`fee` and `confirmed_only` reach `create_self_transfer` too."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    coin = wallet.get_utxo(mark_as_spent=False, confirmed_only=True)

    tx = wallet.send_self_transfer(fee=4321, confirmed_only=True)

    assert tx.vin[0].prev_out == coin.outpoint
    assert coin.value - tx.vout[0].value == 4321


def test_create_self_transfer_multi_defaults_are_core_s_own() -> None:
    """Version 2, `nLockTime` 0 and every input's own `nSequence` 0."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 2)
    coins = wallet.get_utxos()[:2]

    tx = wallet.create_self_transfer_multi(utxos_to_spend=coins)

    assert tx.version == 2
    assert tx.lock_time == 0
    assert [tx_in.sequence for tx_in in tx.vin] == [0, 0]


def test_create_self_transfer_multi_sets_one_sequence_on_every_input() -> None:
    """A bare `sequence` is every input's; `version`, `locktime` the tx's."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 2)
    coins = wallet.get_utxos()[:2]

    tx = wallet.create_self_transfer_multi(
        utxos_to_spend=coins, version=3, locktime=42, sequence=0xFFFFFFFE
    )

    assert tx.version == 3
    assert tx.lock_time == 42
    assert [tx_in.sequence for tx_in in tx.vin] == [0xFFFFFFFE] * 2


def test_create_self_transfer_multi_sets_a_sequence_per_input() -> None:
    """A list of `nSequence` values is paired with the coins, in order."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 2)
    coins = wallet.get_utxos()[:2]

    tx = wallet.create_self_transfer_multi(utxos_to_spend=coins, sequence=[3, 4])

    assert [tx_in.prev_out for tx_in in tx.vin] == [coin.outpoint for coin in coins]
    assert [tx_in.sequence for tx_in in tx.vin] == [3, 4]


def test_create_self_transfer_multi_refuses_too_few_sequences() -> None:
    """One `nSequence` for two coins is refused, `wallet.py`'s own assert."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 2)
    coins = wallet.get_utxos()[:2]
    with pytest.raises(ValueError, match="1 sequence"):
        wallet.create_self_transfer_multi(utxos_to_spend=coins, sequence=[3])


def test_send_self_transfer_multi_forwards_version_locktime_and_sequence() -> None:
    """The three fields reach `create_self_transfer_multi` through this call."""
    rpc, wallet, _ = _funded_wallet()

    tx = wallet.send_self_transfer_multi(version=3, locktime=8, sequence=6)

    assert rpc.sent == [tx.serialize(True, check_validity=False).hex()]
    assert (tx.version, tx.lock_time, tx.vin[0].sequence) == (3, 8, 6)


def test_send_to_spends_with_core_s_own_fields() -> None:
    """`create_self_transfer(fee_rate=0)`'s fields, as `wallet.py` builds."""
    _, wallet, _ = _funded_wallet()

    tx = wallet.send_to(nulldata_script_pub_key(b""), 1)

    assert (tx.version, tx.lock_time, tx.vin[0].sequence) == (2, 0, 0)


@pytest.mark.parametrize(
    "create",
    [
        lambda wallet: wallet.create_self_transfer(),
        lambda wallet: wallet.create_self_transfer_multi(num_outputs=2),
    ],
    ids=["create_self_transfer", "create_self_transfer_multi"],
)
def test_a_created_tx_caches_nothing(create: Callable[[MiniWallet], Tx]) -> None:
    """Core's own `scan_tx` runs on send (`wallet.py`), never on create."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = create(wallet)

    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid=tx.id.hex())


def test_a_tx_edited_after_creation_leaves_no_stale_coin() -> None:
    """Core's `wallet_anchor.py` shape: the next spend takes a real coin.

    The anchor tx gains an output after it is built, which changes its
    txid; a coin cached under the txid it was built with would be the
    largest matured one, spent by nothing that exists.
    """
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)  # two coinbases matured

    anchor_tx = wallet.create_self_transfer(fee_rate=0, version=3)
    anchor_tx.vout.append(TxOut(0, nulldata_script_pub_key(b"")))
    anchor_spend = wallet.create_self_transfer(version=3)

    assert anchor_tx.vin[0].prev_out == OutPoint(
        bytes.fromhex(_coinbase_txid(rpc, 0)), 0
    )
    assert anchor_spend.vin[0].prev_out == OutPoint(
        bytes.fromhex(_coinbase_txid(rpc, 1)), 0
    )


@pytest.mark.parametrize(
    "send",
    [
        lambda wallet: wallet.send_self_transfer(),
        lambda wallet: wallet.send_self_transfer_multi(num_outputs=2),
        lambda wallet: wallet.send_self_transfer_chain(chain_length=2),
        lambda wallet: wallet.send_to(nulldata_script_pub_key(b""), 1),
    ],
    ids=[
        "send_self_transfer",
        "send_self_transfer_multi",
        "send_self_transfer_chain",
        "send_to",
    ],
)
def test_a_refused_send_caches_nothing(send: Callable[[MiniWallet], object]) -> None:
    """A tx the node did not accept leaves no coin of its own behind."""
    rpc = _FakeRpc(send_answer="not-the-txid")
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    with pytest.raises(TypeError, match="sendrawtransaction answered"):
        send(wallet)

    cached = wallet.get_utxos(include_immature_coinbase=True, mark_as_spent=False)
    assert all(utxo.coinbase for utxo in cached)


def test_new_utxos_lists_the_outputs_paying_this_wallet() -> None:
    """Every output but the padding, at height 0, and the cache unread."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.create_self_transfer_multi(num_outputs=2, target_vsize=400)
    balance = wallet.get_balance()

    utxos = wallet.new_utxos(tx)

    assert utxos == [
        Utxo(OutPoint(tx.id, vout), tx.vout[vout].value, 0, coinbase=False)
        for vout in (0, 1)
    ]
    assert wallet.get_balance() == balance


def test_new_utxos_hashes_the_transaction_once() -> None:
    """The txid is computed once, not once per output."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.create_self_transfer_multi(num_outputs=5)

    with patch.object(
        Tx, "id", new_callable=PropertyMock, return_value=b"\x07" * 32
    ) as txid:
        utxos = wallet.new_utxos(tx)

    assert len(utxos) == 5
    assert txid.call_count == 1


def test_send_to_this_wallets_own_script_caches_both_outputs() -> None:
    """`scan_tx` keeps every output paying the wallet's own script."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)

    tx = wallet.send_to(wallet.script_pub_key, 12345)

    assert wallet.get_utxo(txid=tx.id.hex(), vout=1).value == 12345
    assert wallet.get_utxo(txid=tx.id.hex(), vout=0).value == tx.vout[0].value


def _broadcast(rpc: _FakeRpc, tx: Tx) -> None:
    """Hand `tx` to the node the way a caller bypassing `_send` would."""
    rpc.call("sendrawtransaction", [tx.serialize(True, check_validity=False).hex(), 0])


def test_generate_caches_a_confirmed_coin_this_wallet_did_not_send() -> None:
    """ISS 247's probe: a created tx broadcast directly, then mined.

    Core's own `generate` ends in `rescan_utxos` (`wallet.py`), so the
    coin is there to spend however its transaction reached the node.
    """
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.create_self_transfer()
    _broadcast(rpc, tx)

    wallet.generate(1, confirm=[tx])

    coin = wallet.get_utxo(txid=tx.id.hex())
    assert coin == Utxo(
        OutPoint(tx.id, 0), tx.vout[0].value, COINBASE_MATURITY + 2, coinbase=False
    )
    spend = wallet.send_self_transfer(utxo_to_spend=coin)
    assert spend.vin[0].prev_out == OutPoint(tx.id, 0)


def test_generate_drops_a_cached_coin_a_confirmed_tx_spends() -> None:
    """A tx this wallet did not send spends a coin it still holds."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    coin = wallet.get_utxo(mark_as_spent=False)
    tx = wallet.create_self_transfer(utxo_to_spend=coin)
    _broadcast(rpc, tx)

    wallet.generate(1, confirm=[tx])

    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid=coin.outpoint.tx_id.hex())
    assert wallet.get_utxo(txid=tx.id.hex()).confirmed


def test_generate_scans_confirm_in_block_order() -> None:
    """A later tx of the block spends an earlier one's coin: it stays out."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    parent = wallet.create_self_transfer()
    child = wallet.create_self_transfer(utxo_to_spend=wallet.new_utxos(parent)[0])

    wallet.generate(1, confirm=[parent, child])

    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid=parent.id.hex())
    assert wallet.get_utxo(txid=child.id.hex()).height == COINBASE_MATURITY + 2
    assert rpc.mempool_reads == 1


def test_generate_leaves_out_a_coin_a_mempool_tx_already_spends() -> None:
    """The hazard `rescan_utxos`' own mempool pass guards against.

    The child spends one of the parent's coins and is still in the
    mempool when the parent is mined: that coin is spent, its sibling is
    not.
    """
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    parent = wallet.create_self_transfer_multi(num_outputs=2)
    child = wallet.create_self_transfer(utxo_to_spend=wallet.new_utxos(parent)[0])
    for tx in (parent, child):
        _broadcast(rpc, tx)
    rpc.mempool[child.id.hex()] = child.serialize(True, check_validity=False).hex()

    wallet.generate(1, confirm=[parent])

    with pytest.raises(LookupError, match="vout 0"):
        wallet.get_utxo(txid=parent.id.hex(), vout=0)
    assert wallet.get_utxo(txid=parent.id.hex(), vout=1).confirmed
    assert rpc.mempool_reads == 1


def test_generate_scans_a_confirmed_tx_only_once() -> None:
    """A coin taken from a scanned tx does not come back with the next block."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.create_self_transfer()
    wallet.generate(1, confirm=[tx])
    wallet.get_utxo(txid=tx.id.hex())

    wallet.generate(1, confirm=[tx])

    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid=tx.id.hex())


def test_generate_reads_no_mempool_for_a_tx_this_wallet_sent() -> None:
    """A sent tx's coins are already cached: the scan adds none to check."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.send_self_transfer()

    wallet.generate(1, confirm=[tx])

    assert wallet.get_utxo(txid=tx.id.hex()).confirmed
    assert rpc.mempool_reads == 0


@pytest.mark.parametrize(
    "mempool_answer,tx_answer,match",
    [
        ({"txids": []}, None, "getrawmempool answered"),
        (["ab" * 32], 7, "getrawtransaction answered"),
    ],
    ids=["getrawmempool", "getrawtransaction"],
)
def test_generate_refuses_a_bad_mempool_answer(
    mempool_answer: object, tx_answer: object, match: str
) -> None:
    """`_mempool_spends` refuses an answer it cannot read a spend off."""
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.create_self_transfer()
    fake_call = rpc.call

    def _call(method: str, params: list[object] | None = None) -> object:
        if method == "getrawmempool":
            return mempool_answer
        if method == "getrawtransaction":
            return tx_answer
        return fake_call(method, params)

    with (
        patch.object(rpc, "call", _call),
        pytest.raises(TypeError, match=match),
    ):
        wallet.generate(1, confirm=[tx])


def test_generate_drops_a_coin_a_sent_child_in_the_same_block_spends() -> None:
    """An unsent parent and a sent child confirmed together: the coin is spent.

    The child's own send already scanned it, so the scan of the parent
    caches a coin the child, mined in the same block, spends.
    """
    rpc = _FakeRpc()
    wallet = MiniWallet(_FakeNode(rpc))
    wallet.generate(COINBASE_MATURITY + 1)
    parent = wallet.create_self_transfer()
    _broadcast(rpc, parent)
    child = wallet.send_self_transfer(utxo_to_spend=wallet.new_utxos(parent)[0])

    wallet.generate(1, confirm=[parent, child])

    with pytest.raises(LookupError, match="no coin"):
        wallet.get_utxo(txid=parent.id.hex())
    assert wallet.get_utxo(txid=child.id.hex()).confirmed
