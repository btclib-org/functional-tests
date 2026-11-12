# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`MiniWallet`: coins without a wallet, cached from blocks it mines itself.

[ISS 4](https://github.com/btclib-org/functional-tests/issues/4), step 5
of [ISS btclib-org/btclib#2220](https://github.com/btclib-org/btclib/issues/2220):
Core's own `MiniWallet` (`test/functional/test_framework/wallet.py`) feeds
its own cache by calling `generatetodescriptor` and then `scantxoutset` --
mining through the node's descriptor-wallet machinery and asking the node
to find its own coins back. This one needs neither RPC: `generate` builds
the coinbase itself (`btclib.block.build.build_coinbase`), mines it
(`btclib.block.mining.mine`) and delivers it over `submitblock`, so the
txid and the vout its own coin sits at are known before the block is ever
submitted rather than scanned back out of the node afterwards.

The scriptPubKey every coin of this class pays is Core's own default
`MiniWalletMode.ADDRESS_OP_TRUE` (`wallet.py`'s own docstring table): a
P2TR output whose internal key is the x-only integer 1 and whose single
tapscript leaf, at leaf version `0xc0`, is bare `OP_TRUE` -- the same
pair `test_framework/address.py`'s own
`create_deterministic_address_bcrt1_p2tr_op_true` and
`test_framework/script.py`'s own `taproot_construct` build.
`btclib.script.taproot.output_pubkey` and
`input_script_sig` build the same output key and the same control block
from that pair, byte for byte: the address this module's own scriptPubKey
serializes to under `regtest` is Core's own published constant,
`bcrt1p9yfmy5h72durp7zrhlw9lf7jpwjgvwdg0jr0lqmmjtgg83266lqsekaqka`, and
the spend it proves is standard under bitcoind's default mempool policy,
needing neither a scriptSig long enough to clear a minimum size nor a
policy flag to admit a scriptPubKey no standard template matches. `OP_TRUE`
alone is still the smaller claim than a real signature (`RAW_P2PK`,
`wallet.py`'s third mode): it proves the mechanism this issue is about --
a cache fed from mined blocks -- without also proving btclib's own
signing surface.

`RAW_P2PK_SCRIPT_PUB_KEY` and `raw_p2pk_script_sig`, alongside the class,
are that third mode's output and its signature, for a caller whose
subject needs a coin spent under a real signature
([ISS 167](https://github.com/btclib-org/functional-tests/issues/167)):
the class never pays one, and the caller pays it -- a coinbase it builds,
typically -- and spends it itself.

`Capability.MINE` is what a caller checks before constructing one, the
same capability the first family already skips on for a node not
mining: not a new one, since this class produces the fact `MINE` already
names -- "a block the node accepts as its own new tip, however it gets
there" -- by client-side construction over `submitblock` rather than a
node's own wallet, exactly the second half `node.py`'s own docstring
already draws. `BtclibNodeAdapter` declares it only on a build that
connects a submitted block with no peer, and its own `mine` is this
class's `generate` (`btclib_node.py`'s own docstring has both).

[ISS 4](https://github.com/btclib-org/functional-tests/issues/4)'s own
remaining ports add several things beyond the mechanism above. Two are
read off Core's own `wallet.py` rather than invented: `get_utxo` finds a
specific cached coin by its own txid, with no maturity filter -- a caller
naming one by hand is presumed to know what it is asking for,
`mempool_spend_coinbase.py`'s own subject being what a *node* does with an
immature one; `create_self_transfer` and `send_self_transfer` both take an
optional `utxo_to_spend`, spending the named coin in place of the one
`get_utxo` would pick. The other two have no counterpart there.
`resync` re-reads this wallet's own tip, height and median-time the way
`__init__` does, for a chain an `invalidateblock` or a `build_fork`
submission moved without this wallet's own `generate` doing it.
`rescan_utxos` (`wallet.py`) is the shape not taken: it also rebuilds the
coin cache from `scantxoutset`, which this module's own docstring already
has why nothing here ever calls; `resync` leaves the cache's membership
for the caller to reconcile against whatever the RPC that exposed the
reorg already told it.

A cached coin's own `height` is the fact `confirmed_only` filters on,
`get_utxo`'s and `get_utxos`' alike (`wallet.py`): the block that holds
it, or `0` for a coin no block holds as far as this wallet knows.
`wallet.py` keeps a separate `confirmations` beside a height that is `0`
exactly when that count is, and filters on the count being positive;
`Utxo.confirmed` is that same test read off the height. `generate` sets
it for the cached coins a transaction its own `confirm` carries pays.
A block mined
anywhere else -- by another node, or by `generatetoaddress` on this one,
each filling it from a mempool -- confirms a coin this wallet cannot see
from here, so `resync` also asks the node: `gettxout` with
`include_mempool` false, for every cached coin that is not a coinbase,
`rescan_utxos`' own role in the one respect a `confirmed_only` caller
needs. `gettxout` rather than `getrawtransaction`: it answers from the
chainstate's own UTXO set, where `getrawtransaction` finds a transaction
outside the mempool only with `-txindex` or its block's hash, neither of
which this wallet has.

`generate`'s own `confirm` is the second: no Core file has it, because
Core's own node-side mining (`generatetodescriptor`) pulls the whole
mempool into whatever it mines, and this class's mining reads no mempool
back to do the same (this module's own docstring already has why). A
caller that broadcast a transaction and wants it mined names it here,
already knowing it -- `send_self_transfer`'s own return value, typically
-- rather than this class fetching `getrawmempool` and
`getrawtransaction` back to rediscover what it already handed the node.

[ISS 247](https://github.com/btclib-org/functional-tests/issues/247):
a `confirm` transaction this wallet never sent -- built by a `create_*`
method and broadcast some other way, over `submitpackage`, p2p relay or
a raw `sendrawtransaction` -- is scanned into the cache once its block
is accepted, where Core's own `generate` (`wallet.py`) finds the same
coins by ending in `rescan_utxos`. The scan is `_scan_tx`'s, in block
order: a cached coin the transaction spends is dropped, and every output
paying this wallet is cached at that block's height. A transaction is
scanned once, by `_send` or by `generate`, so a coin a caller already
took from one is not cached again by a later `confirm` naming it; one
already scanned still drops the cached coins it spends, which an unsent
parent carried in the same block may just have cached.
`rescan_utxos` rebuilds the whole cache from `scantxoutset` instead, an
RPC not every node serves (`Capability.SCAN_UTXO_SET`), and one that
would also cache every coin of this script that any other `MiniWallet`
on the same node holds. Its mempool pass is kept in the one respect that
decides a scanned coin: a coin the scan adds is left out where a
transaction in the node's mempool already spends it -- a spend of one of
`new_utxos`' own coins, broadcast before its parent was mined -- read
off `getrawmempool` and `getrawtransaction`, only when the scan added a
coin. What `rescan_utxos` caches and this does not: a coin a block mined
elsewhere pays, a coin only a mempool transaction pays, and a coin a
caller took from the cache and never spent.

`build_fork`, alongside the class, is `create_empty_fork`
(`test/functional/test_framework/blocktools.py`): unsubmitted blocks
extending whatever tip the node it is given actually has, for a caller
that wants to hold them back and submit them later, `mempool_resurrect.py`'s
own subject. It shares no wallet state -- a fork is disposable by
construction, spent by nobody -- so it reads the node fresh rather than a
`MiniWallet` instance's own cache, and pays whichever `script_pub_key` its
caller names, that caller's own wallet's `script_pub_key` ordinarily.

`build_next_block` is one block rather than a fork: solved, unsubmitted,
on the node's own current tip, carrying the transactions its caller
names beside a coinbase paying the `script_pub_key` it names, at a
header version and a time it may choose -- Core's own `create_block`
(`blocktools.py`), whose coinbase is `create_coinbase`'s. Submitting it
is left to the caller, which reads `submitblock`'s own answer, a refusal
as much as an acceptance, or submits it inside `assert_debug_log` to read
bitcoind's own log line instead.

[ISS 14](https://github.com/btclib-org/functional-tests/issues/14)'s
own `mempool_package_limits.py` and `mempool_updatefromblock.py` need a
coin cache several transactions deep rather than one spend at a time:
`create_self_transfer_multi` spends one or several cached coins into
several outputs at once, Core's own `create_self_transfer_multi`
(`wallet.py`), and `create_self_transfer_chain` spends the output each
call just created into the next, Core's own `create_self_transfer_chain`.
Both, and `create_self_transfer` alongside them, take an optional
`target_vsize`: an `OP_RETURN` output of literal `OP_1` opcodes (no
`PUSHDATA` of its own to size around) pads the transaction to exactly
that many virtual bytes, `_pad_to_vsize` mirroring Core's own `bulk_vout`
(`test_framework/script_util.py`) byte for byte. `get_utxo` gains a
`vout` beside `txid` for the same reason Core's own carries one: a
multi-output send caches more than one coin under the same `txid`, and a
caller spending them in an order its own loop chooses rather than the
order they were cached needs the pair rather than the first match alone.

[ISS 103](https://github.com/btclib-org/functional-tests/issues/103):
an RBF or TRUC test's subject is the fee, the sequence or the version, so
the self-transfers take what Core's own take -- `fee_rate`, `fee`,
`version`, `locktime` and `sequence` on `create_self_transfer`, the last
three on `create_self_transfer_multi` -- with Core's own defaults and fee
arithmetic. Where Core's own take BTC `Decimal`s these take satoshis, and
satoshis per 1000 virtual bytes for a rate: the unit `fee_per_output`
already has in Core, and `create_self_transfer`'s own docstring has what
else differs.

[ISS 242](https://github.com/btclib-org/functional-tests/issues/242):
the cache learns of a transaction when this class sends it, as Core's
own `scan_tx` runs in its `sendrawtransaction` (`wallet.py`), or when
`generate` confirms it (ISS 247, above). A `create_*` method adds nothing
to the cache, `get_utxo` taking out the coin it picks for one, so a
transaction that is refused, or is edited after it is built, leaves no
coin behind for the next spend to pick, and neither does one that is
never sent until a `confirm` names it.
`new_utxos` is Core's own `new_utxos`, the coins a transaction pays this
wallet, less the padding output `target_vsize` adds: a caller spending
one before sending it, or broadcasting it some other way, names that
coin by hand until a block confirms it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from btclib import var_int
from btclib.alias import TaprootScriptTree
from btclib.block.block import Block
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import VERSION, mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS, subsidy
from btclib.key import PrvKeyData, PubKeyData
from btclib.script import sig_hash
from btclib.script.script import serialize as script_serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.script.taproot import input_script_sig
from btclib.script.taproot import serialize as tapscript_serialize
from btclib.script.witness import Witness
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY
from ecc.dsa import sign_

if TYPE_CHECKING:
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "DEFAULT_FEE_RATE",
    "FEE",
    "PADDING_DATACARRIER_SIZE",
    "RAW_P2PK_SCRIPT_PUB_KEY",
    "MiniWallet",
    "Utxo",
    "build_fork",
    "build_next_block",
    "nulldata_script_pub_key",
    "raw_p2pk_script_sig",
]

# Core's own ADDRESS_OP_TRUE internal key (`test_framework/address.py`'s
# own `create_deterministic_address_bcrt1_p2tr_op_true`): the x-only
# integer 1, prefixed 02 for `PubKeyData` -- the prefix names the lift as
# BIP341's own even one and adds nothing the tweak reads
_INTERNAL_PUBKEY = PubKeyData(b"\x02" + (1).to_bytes(32, "big"), check_validity=False)

# the single tapscript leaf ADDRESS_OP_TRUE carries -- leaf version 0xc0,
# Core's own `taproot_construct` default -- bare OP_TRUE, needing no
# signature and proving Core's own script path rather than its key path
_SCRIPT_TREE: TaprootScriptTree = [(0xC0, ["OP_TRUE"])]

# the anyone-can-spend P2TR output every coin of this class pays;
# `regtest`, this module's only chain, decides the address the taproot
# output key serializes to and nothing about the output key itself
_ANYONE_CAN_SPEND = ScriptPubKey.p2tr(_INTERNAL_PUBKEY, _SCRIPT_TREE, network="regtest")

# satoshis, a fixed fee: this class asks the node for nothing to compute
# one, the same shape Core's own `send_to` (`wallet.py`) takes and for
# the same reason
FEE = 1000

# satoshis per 1000 virtual bytes: Core's own `create_self_transfer` default
# (`wallet.py`), `Decimal("0.003")` BTC/kvB
DEFAULT_FEE_RATE = 300_000

# the `-datacarriersize` Core's `fill_mempool` requires
# (`test_framework/mempool_util.py`), over the `OP_RETURN` padding
# `target_vsize` adds to a transaction of up to 100000 virtual bytes; a
# bitcoind before bitcoin/bitcoin#32406 refuses that padding by default, with
# `scriptpubkey`
PADDING_DATACARRIER_SIZE = 100_000

# regtest's own halving schedule (`CONSENSUS_PARAMS["regtest"]`): every 150
# blocks rather than mainnet's 210_000, `subsidy`'s own default -- this
# module's only chain, so `generate` reads this rather than that default
_REGTEST_SUBSIDY_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval


@dataclass(frozen=True)
class Utxo:
    """One coin `MiniWallet` knows about, spendable with `_witness()` alone.

    `height` is the block holding the coin, `0` where no block does as far
    as the wallet knows -- this module's own docstring has how it learns.
    """

    outpoint: OutPoint
    value: int
    height: int
    coinbase: bool

    @property
    def confirmed(self) -> bool:
        """Return whether a block holds this coin, `confirmed_only`'s test."""
        return self.height > 0


def _read_tip(node: NodeAdapter) -> tuple[bytes, int, int]:
    """Return `(tip, height, median_time)`, read fresh off `node`'s own RPC.

    Shared by the constructor, `MiniWallet.resync`, `build_fork` and
    `build_next_block`: each
    needs to know where the chain actually is right now rather than
    trusting a cached view that an out-of-band `invalidateblock` or a
    `build_fork` submission may have moved since it was last read.

    :param node: the node to read.
    :raises TypeError: `getbestblockhash`, `getblockcount` or
        `getblockchaininfo` answered something this call cannot use.
    """
    best_hash = node.rpc.call("getbestblockhash")
    if not isinstance(best_hash, str):
        err_msg = f"getbestblockhash answered {best_hash!r}, not a hash string"
        raise TypeError(err_msg)
    height = node.rpc.call("getblockcount")
    if not isinstance(height, int):
        err_msg = f"getblockcount answered {height!r}, not an int"
        raise TypeError(err_msg)
    chain_info = node.rpc.call("getblockchaininfo")
    median_time = chain_info.get("mediantime") if isinstance(chain_info, dict) else None
    if not isinstance(median_time, int):
        err_msg = f"getblockchaininfo answered {chain_info!r}, no int mediantime"
        raise TypeError(err_msg)
    return bytes.fromhex(best_hash), height, median_time


def _mine_one(
    tip: bytes,
    height: int,
    min_time: int,
    script_pub_key: ScriptPubKey,
    extra_transactions: Sequence[Tx] = (),
    *,
    version: int = VERSION,
    extra_output_script: ScriptPubKey | None = None,
    block_time: int | None = None,
) -> tuple[Block, int]:
    """Build, solve and return one block extending `tip`, and its own time.

    Shared by `MiniWallet.generate`, `build_fork` and `build_next_block`:
    each mines a block through the same client-side path --
    `build_coinbase` and `build_block` (`btclib.block.build`), then `mine`
    (`btclib.block.mining`) -- differing in what becomes of the result
    afterwards, submitted and cached by the first and held for its caller
    to submit by the other two, and in what rides along beside the
    coinbase.

    :param tip: the previous block's own header hash.
    :param height: this block's own height.
    :param min_time: this block's own time must exceed this -- the
        previous block's own time, or the chain's median-time-past for
        the first block of a run.
    :param script_pub_key: what this block's coinbase pays.
    :param extra_transactions: already-broadcast transactions to carry
        along, in the order given -- a caller's own to have ordered so
        that a spend of one of them sits after it, since this class reads
        no mempool back to order them itself. `MiniWallet.generate`'s own
        `confirm` is the caller-facing name for this.
    :param version: the block header's own version.
    :param extra_output_script: where given, the coinbase carries a
        second, zero-valued output paying it.
    :param block_time: where given, this block's own time, as given and
        with `min_time` unread.
    :returns: the solved block, and the time it carries.
    :raises RuntimeError: `mine` exhausted its own search bound without
        solving one -- regtest's own target is wide enough that this is
        not expected to happen.
    """
    coinbase = build_coinbase(
        height, script_pub_key, halving_interval=_REGTEST_SUBSIDY_HALVING_INTERVAL
    )
    if extra_output_script is not None:
        coinbase.vout.append(TxOut(0, extra_output_script))
    if block_time is None:
        block_time = max(int(datetime.now(UTC).timestamp()), min_time + 1)
    candidate = build_block(
        tip,
        [coinbase, *extra_transactions],
        datetime.fromtimestamp(block_time, UTC),
        REGTEST_POW_LIMIT_BITS,
        version=version,
    )
    solved = mine(candidate.header)
    if solved is None:
        err_msg = f"no nonce solved height {height} within the search bound"
        raise RuntimeError(err_msg)
    return Block(solved, candidate.transactions, check_validity=False), block_time


def build_fork(
    node: NodeAdapter, script_pub_key: ScriptPubKey, length: int
) -> list[Block]:
    """Return `length` unsubmitted blocks extending `node`'s own current tip.

    `create_empty_fork`
    (`test/functional/test_framework/blocktools.py`): each block pays
    `script_pub_key` and carries no other transaction, mined client-side
    the same way `MiniWallet.generate` mines one, and none of them is
    submitted -- the caller does that, in order, once it wants to test
    what happens when this fork turns out to carry more work than
    whatever the node accepted in the meantime.

    Independent of any `MiniWallet` instance's own cache: a fork built
    this way is spent by nobody, so nothing here needs one.

    :param node: the node whose own current tip this fork extends.
    :param script_pub_key: what every block of the fork pays.
    :param length: how many blocks to build.
    :raises RuntimeError: `_mine_one` could not solve one of them.
    :raises TypeError: `node`'s own RPC answered something `_read_tip`
        cannot use.
    """
    tip, height, median_time = _read_tip(node)
    last_time = median_time
    blocks = []
    for _ in range(length):
        height += 1
        block, last_time = _mine_one(tip, height, last_time, script_pub_key)
        blocks.append(block)
        tip = block.header.hash
    return blocks


def build_next_block(
    node: NodeAdapter,
    script_pub_key: ScriptPubKey,
    transactions: Sequence[Tx] = (),
    *,
    version: int = VERSION,
    extra_output_script: ScriptPubKey | None = None,
    time: int | None = None,
) -> Block:
    """Return one solved, unsubmitted block extending `node`'s own current tip.

    `create_block` over `create_coinbase`
    (`test/functional/test_framework/blocktools.py`), mined client-side
    by `_mine_one`, as `MiniWallet.generate` mines one. The tip, the
    height and the median-time-past are read off `node` on every call,
    and the block's own time is `time` where given, otherwise the wall
    clock or one second past that median-time-past, whichever is later.
    Submitting it is the caller's, and so is reading `submitblock`'s own
    answer.

    :param node: the node whose own current tip this block extends.
    :param script_pub_key: what the coinbase pays.
    :param transactions: what the block carries after its coinbase, in
        the order given.
    :param version: the block header's own version;
        `btclib.block.mining.VERSION` where not given, where Core's own
        `create_block` defaults to `4`.
    :param extra_output_script: where given, the coinbase carries a
        second, zero-valued output paying it, Core's own
        `create_coinbase` parameter of the same name.
    :param time: the block header's own time, in seconds since the epoch,
        taken as given rather than checked against the median-time-past:
        Core's own `create_block` `ntime`, for a caller whose subject is
        the time a block carries.
    :raises RuntimeError: `mine` exhausted its own search bound without
        solving it -- regtest's own target is wide enough that this is
        not expected to happen.
    :raises TypeError: `node`'s own RPC answered something `_read_tip`
        cannot use.
    """
    tip, height, median_time = _read_tip(node)
    block, _ = _mine_one(
        tip,
        height + 1,
        median_time,
        script_pub_key,
        transactions,
        version=version,
        extra_output_script=extra_output_script,
        block_time=time,
    )
    return block


def nulldata_script_pub_key(data: bytes) -> ScriptPubKey:
    """Return an `OP_RETURN` scriptPubKey carrying `data`, of any length.

    `CScript([OP_RETURN, data])` (`test_framework/script.py`), not
    `ScriptPubKey.nulldata`: that classmethod's own 80-byte refusal is
    the historical "standard" bound, which a `-datacarriersize` test asks
    a *node* about rather than a fact this library should enforce before
    the request ever leaves this process
    ([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)).
    `check_validity=False`: `ScriptPubKey.assert_valid` runs no length
    check of its own on a nulldata script, so this only ever skips the
    network-name check `Script.__init__` would otherwise repeat.

    :param data: the payload to push after `OP_RETURN`; any length,
        including one `ScriptPubKey.nulldata` would refuse.
    """
    return ScriptPubKey(script_serialize(["OP_RETURN", data]), check_validity=False)


# Core's own `RAW_P2PK` key (`wallet.py`'s own `MiniWallet.__init__`): the
# private key 1, whose public key is secp256k1's own generator
_RAW_P2PK_KEY = PrvKeyData(1, "regtest")

# the pay-to-public-key output of `_RAW_P2PK_KEY`, Core's own `RAW_P2PK`
# scriptPubKey (`key_to_p2pk_script`, `test_framework/script_util.py`)
RAW_P2PK_SCRIPT_PUB_KEY = ScriptPubKey.p2pk(_RAW_P2PK_KEY.pub)


def raw_p2pk_script_sig(tx: Tx, vin_i: int) -> bytes:
    """Return the scriptSig spending a `RAW_P2PK_SCRIPT_PUB_KEY` coin.

    `sign_tx` (`wallet.py`) in its `RAW_P2PK` mode, `sign_input_legacy`
    (`test_framework/script_util.py`): the one push of a `SIGHASH_ALL`
    ECDSA signature over `btclib.script.sig_hash.legacy`. That sighash
    blanks every input's scriptSig, so `tx` may carry any when this is
    called, and the caller puts the answer in place.

    :param tx: the spending transaction.
    :param vin_i: the index of the input spending the coin.
    """
    digest = sig_hash.legacy(RAW_P2PK_SCRIPT_PUB_KEY.script, tx, vin_i, sig_hash.ALL)
    signature = sign_(digest, _RAW_P2PK_KEY.q).serialize()
    return script_serialize([signature + bytes([sig_hash.ALL])])


def _pad_to_vsize(tx: Tx, target_vsize: int) -> None:
    """Append an `OP_RETURN` output padding `tx` to exactly `target_vsize`.

    `bulk_vout` (`test_framework/script_util.py`): the padding output's
    own script is literal `OP_1` opcodes after the `OP_RETURN`, one byte
    each and no `PUSHDATA` of its own to size around, unlike
    `nulldata_script_pub_key`'s pushed payload -- which is why the
    placeholder below is a bare `OP_RETURN` rather than that function's
    empty push, one byte rather than two. `tx.vout` gains one output;
    nothing already there is touched.

    :param tx: the transaction to pad, mutated in place.
    :param target_vsize: the virtual size `tx` must reach.
    :raises ValueError: `tx` is already at or past `target_vsize` before
        the empty placeholder output below is even added.
    """
    placeholder = ScriptPubKey(script_serialize(["OP_RETURN"]), check_validity=False)
    tx.vout.append(TxOut(0, placeholder))
    deficit = target_vsize - tx.vsize
    if deficit < 0:
        err_msg = f"target_vsize {target_vsize} is smaller than {tx.vsize}"
        raise ValueError(err_msg)
    # the compact-size length prefix ahead of the padding script grows as
    # padding is added; the placeholder script above already accounts for
    # one byte of it, so only the growth beyond that one byte is still owed
    deficit -= len(var_int.serialize(deficit)) - 1
    padding_script = ScriptPubKey(
        script_serialize(["OP_RETURN", *(["OP_1"] * deficit)]), check_validity=False
    )
    tx.vout[-1] = TxOut(0, padding_script)


def _witness() -> Witness:
    """Return the script-path witness that spends `_ANYONE_CAN_SPEND`.

    `input_script_sig` returns the one leaf and the control block that
    proves it against the output key; the witness stack is the leaf
    script serialized (BIP342's `serialize`, not the ordinary script
    codec, `_SCRIPT_TREE` carrying no `OP_SUCCESS` this call would need
    to differ on) followed by the control block, per BIP341.
    """
    leaf_script, control_block = input_script_sig(_INTERNAL_PUBKEY, _SCRIPT_TREE, 0)
    return Witness([tapscript_serialize(leaf_script), control_block])


class MiniWallet:
    """Coins without a wallet: a UTXO cache fed from blocks this class mines.

    This module's own docstring is the design; `Capability.MINE`
    (`capability.py`) is what a caller checks before constructing one.

    :param node: the node this wallet mines into and spends against, its
        `rpc` the whole of how this class ever reaches it -- no
        in-process import of anything the node itself runs.
    """

    def __init__(self, node: NodeAdapter) -> None:
        self._node = node
        self._utxos: list[Utxo] = []
        # the txid of every transaction `_scan_tx` has scanned, so that
        # `generate` scans a `confirm` transaction only where no send did
        self._scanned: set[bytes] = set()
        # seeded from the existing chain's own median-time-past rather than
        # 0: a node this wallet did not mine into -- the session-scoped
        # `bitcoind_adapter` fixture hands more than one MiniWallet the same
        # node -- already has a tip whose median-time-past this wallet's
        # first block must exceed, `time-too-old` otherwise
        self._tip, self._height, self._last_time = _read_tip(node)

    def resync(self) -> None:
        """Re-read the tip from the node, and which cached coins a block holds.

        For a chain move this wallet did not itself make -- an
        `invalidateblock`, or a `build_fork` a caller has just submitted --
        so that the next `generate` extends the chain that is actually
        there instead of a tip this wallet last saw before it moved.
        No coin is added to the cache or dropped from it: this class has
        no `scantxoutset` to rebuild it from (this module's own docstring
        has why), so a coin the move spent or unspent again is the
        caller's own to reconcile, from whatever RPC told it the move
        happened.

        What changes is each cached non-coinbase coin's own `height`, as
        `gettxout` answers it with `include_mempool` false: the block
        holding the coin where the chainstate's UTXO set has it, `0`
        where it does not -- a coin only the mempool holds, or one a
        block has spent. A coinbase is left as it is, `generate` having
        cached it at the height of the block that created it.

        :raises TypeError: `getbestblockhash`, `getblockcount`,
            `getblockchaininfo` or `gettxout` answered something this call
            cannot use.
        """
        self._tip, self._height, self._last_time = _read_tip(self._node)
        for index, utxo in enumerate(self._utxos):
            if utxo.coinbase:
                continue
            answer = self._node.rpc.call(
                "gettxout", [utxo.outpoint.tx_id.hex(), utxo.outpoint.vout, False]
            )
            if answer is None:
                height = 0
            else:
                confirmations = (
                    answer.get("confirmations") if isinstance(answer, dict) else None
                )
                if not isinstance(confirmations, int) or confirmations < 1:
                    err_msg = f"gettxout answered {answer!r}, no confirmations"
                    raise TypeError(err_msg)
                height = self._height - confirmations + 1
            self._utxos[index] = replace(utxo, height=height)

    @property
    def script_pub_key(self) -> ScriptPubKey:
        """Return the anyone-can-spend script every coin of this wallet pays."""
        return _ANYONE_CAN_SPEND

    def get_balance(self) -> int:
        """Return the satoshi sum of every coin this wallet still holds."""
        return sum(utxo.value for utxo in self._utxos)

    def generate(self, count: int, *, confirm: Sequence[Tx] = ()) -> list[bytes]:
        """Mine `count` blocks paying this wallet's own script, and cache them.

        Client-side start to finish: `build_coinbase` and `build_block`
        (`btclib.block.build`) build the block, `mine`
        (`btclib.block.mining`) solves its nonce, and `submitblock`
        delivers it -- this module's own docstring has why nothing here
        ever calls `scantxoutset`. The block's own time is strictly
        later than the last one this call or a previous one produced, and
        than the chain's own median-time-past at construction: bitcoind
        refuses `time-too-old` for a block whose own time does not exceed
        its chain's own median-time-past, which two blocks minted in the
        same wall-clock second otherwise both carry, and which a second
        wallet built on top of a chain it did not itself mine otherwise
        carries from its very first block.

        :param count: how many blocks to mine.
        :param confirm: already-broadcast transactions to carry in the
            first of the `count` blocks -- `send_self_transfer`'s own
            return value, typically. Bitcoind's own node-side mining pulls
            the whole mempool in on every block; this class reads no
            mempool back to fill one, so a caller names exactly what it wants
            confirmed, in an order where a spend of one of them already
            sits after it. One this wallet has not scanned yet -- never
            sent through it -- is scanned once the block is accepted, as
            `_scan_tx` scans a send, except that a coin the scan adds is
            left out where a transaction in the node's mempool already
            spends it: this module's own docstring has the choice and its
            limits. Every cached coin one of them pays takes that block's
            own height, which is what `confirmed_only` reads.
        :returns: the mined blocks' own header hashes, display order,
            oldest first.
        :raises RuntimeError: `mine` exhausted its own search bound
            without solving one -- regtest's own target is wide enough
            that this is not expected to happen.
        :raises TypeError: `submitblock` answered anything but
            acceptance (`None`), or `getrawmempool` or
            `getrawtransaction` something `_mempool_spends` cannot use.
        """
        hashes = []
        for index in range(count):
            height = self._height + 1
            extra_transactions = confirm if index == 0 else ()
            block, block_time = _mine_one(
                self._tip,
                height,
                self._last_time,
                _ANYONE_CAN_SPEND,
                extra_transactions,
            )
            answer = self._node.rpc.call(
                "submitblock", [block.serialize(check_validity=False).hex()]
            )
            if answer is not None:
                err_msg = f"submitblock refused height {height}: {answer!r}"
                raise TypeError(err_msg)
            added: set[OutPoint] = set()
            for tx in extra_transactions:
                if tx.id not in self._scanned:
                    added.update(utxo.outpoint for utxo in self._scan_tx(tx))
                else:
                    # its own send dropped what it spent then; an unsent
                    # parent this block also carries may have cached one since
                    spent = {tx_in.prev_out for tx_in in tx.vin}
                    self._utxos = [u for u in self._utxos if u.outpoint not in spent]
            confirmed_ids = {tx.id for tx in extra_transactions}
            self._utxos = [
                replace(utxo, height=height)
                if utxo.outpoint.tx_id in confirmed_ids
                else utxo
                for utxo in self._utxos
            ]
            if added:
                spent = added & self._mempool_spends()
                self._utxos = [u for u in self._utxos if u.outpoint not in spent]
            self._utxos.append(
                Utxo(
                    outpoint=OutPoint(block.transactions[0].id, 0),
                    value=subsidy(height, _REGTEST_SUBSIDY_HALVING_INTERVAL),
                    height=height,
                    coinbase=True,
                )
            )
            self._tip = block.header.hash
            self._height = height
            self._last_time = block_time
            hashes.append(self._tip)
        return hashes

    def _is_mature(self, utxo: Utxo) -> bool:
        """Return whether `utxo` is spendable in the next block.

        Matured: a coinbase spent at `self._height + 1` -- the earliest
        height a transaction broadcast now could be mined at -- clears
        `COINBASE_MATURITY`, the threshold Core's own `get_utxo` and
        `get_utxos` (`wallet.py`) apply; a non-coinbase coin always is.
        `self._height` is this wallet's own tip, where `wallet.py` asks
        `getblockchaininfo` for it: `resync` is what brings it forward
        past a block this wallet did not mine.
        """
        return not utxo.coinbase or self._height + 1 - utxo.height >= COINBASE_MATURITY

    def get_utxo(
        self,
        *,
        txid: str | None = None,
        vout: int | None = None,
        mark_as_spent: bool = True,
        confirmed_only: bool = False,
    ) -> Utxo:
        """Return a cached coin, forgetting it unless `mark_as_spent` is false.

        `get_utxo` (`wallet.py`), in its order: the cache is first sorted
        in place by value, then by descending height, so the largest coin
        sits last and, among equal values, the lowest height after the
        higher. Without `txid` the answer is the largest matured coin, the
        lowest height of equal ones -- a coin no block holds, at `0`,
        ahead of a confirmed one. With it, the answer is the first coin in
        that order `txid` paid, maturity aside: a caller naming a coin by
        its own txid is presumed to already know what it is,
        `mempool_spend_coinbase.py`'s own subject being what the *node*
        does when handed a spend of one that has not cleared
        `COINBASE_MATURITY` yet.

        :param txid: the hex txid of the coin's own transaction; the
            largest matured coin where `None`.
        :param vout: the coin's own output index, where more than one
            cached coin shares `txid` -- `send_self_transfer_multi`'s
            own several outputs -- and a caller wants a specific one
            rather than whichever the order above puts first.
        :param mark_as_spent: where false, the coin is returned and stays
            cached, for a caller that will spend it itself; sending the
            transaction that spends it drops it then.
        :param confirmed_only: only a coin a block holds (`Utxo.confirmed`).
        :raises LookupError: no cached coin answers every filter given --
            `wallet.py`'s own `next` raising `StopIteration` instead.
        """
        self._utxos.sort(key=lambda utxo: (utxo.value, -utxo.height))
        if txid is not None:
            candidates = [u for u in self._utxos if u.outpoint.tx_id.hex() == txid]
        else:
            candidates = [u for u in reversed(self._utxos) if self._is_mature(u)]
        if vout is not None:
            candidates = [u for u in candidates if u.outpoint.vout == vout]
        if confirmed_only:
            candidates = [u for u in candidates if u.confirmed]
        if not candidates:
            if txid is not None:
                err_msg = f"no coin of this wallet was paid by txid {txid!r}"
            else:
                err_msg = "no coin of this wallet has matured yet"
            if vout is not None:
                err_msg += f", vout {vout}"
            if confirmed_only:
                err_msg += ", confirmed"
            raise LookupError(err_msg)
        index = self._utxos.index(candidates[0])
        return self._utxos.pop(index) if mark_as_spent else self._utxos[index]

    def get_utxos(
        self,
        *,
        include_immature_coinbase: bool = False,
        mark_as_spent: bool = True,
        confirmed_only: bool = False,
    ) -> list[Utxo]:
        """Return every cached coin the filters keep, in the cache's order.

        `get_utxos` (`wallet.py`): no sort of its own, so the order is
        whatever the last `get_utxo` sorted the cache into, followed by
        what was cached after it. `mark_as_spent` forgets the whole
        cache, not only the coins returned -- an immature coinbase and a
        coin no block holds are dropped with the rest, as `wallet.py`'s
        own `self._utxos = []` drops them.

        :param include_immature_coinbase: keep a coinbase that has not
            matured yet (`_is_mature`).
        :param mark_as_spent: where true, empty the cache.
        :param confirmed_only: only coins a block holds (`Utxo.confirmed`).
        """
        utxos = [
            utxo
            for utxo in self._utxos
            if (include_immature_coinbase or self._is_mature(utxo))
            and (not confirmed_only or utxo.confirmed)
        ]
        if mark_as_spent:
            self._utxos = []
        return utxos

    def new_utxos(self, tx: Tx) -> list[Utxo]:
        """Return the coins `tx` pays this wallet, in output order, uncached.

        `new_utxos` (`wallet.py`), which Core's own `create_*` return beside
        the transaction, and whose first entry is its `new_utxo`. Core's
        lists every output, a `target_vsize` padding one included; this one
        lists the outputs paying `script_pub_key`, the ones `_witness()`
        spends, which lead every transaction a `create_*` method builds.
        Each is at height `0`, and the cache is neither read nor changed.

        :param tx: the transaction, sent or not.
        """
        txid = tx.id
        return [
            Utxo(
                outpoint=OutPoint(txid, vout),
                value=tx_out.value,
                height=0,
                coinbase=False,
            )
            for vout, tx_out in enumerate(tx.vout)
            if tx_out.script_pub_key.script == _ANYONE_CAN_SPEND.script
        ]

    def _scan_tx(self, tx: Tx) -> list[Utxo]:
        """Forget every cached coin `tx` spends; cache and return what it pays.

        `scan_tx` (`wallet.py`), for a transaction this class holds and so
        need not decode: an input naming a cached coin -- one a caller
        took with `mark_as_spent` false -- drops it, and `new_utxos`' own
        answer for `tx` joins the cache. `tx`'s own txid joins the set
        `generate` reads to scan a `confirm` transaction only once.
        """
        spent = {tx_in.prev_out for tx_in in tx.vin}
        self._utxos = [u for u in self._utxos if u.outpoint not in spent]
        new = self.new_utxos(tx)
        self._utxos.extend(new)
        self._scanned.add(tx.id)
        return new

    def _mempool_spends(self) -> set[OutPoint]:
        """Return every outpoint a transaction in the node's mempool spends.

        `rescan_utxos`' own mempool pass (`wallet.py`), which runs
        `scan_tx` over every mempool transaction, in the one respect
        `generate` needs: the coins those transactions spend. Read off
        `getrawmempool` and each txid's own `getrawtransaction`, whose hex
        answer `Tx.parse` decodes.

        :raises TypeError: `getrawmempool` answered something other than a
            list, or `getrawtransaction` something other than a hex string.
        """
        txids = self._node.rpc.call("getrawmempool")
        if not isinstance(txids, list):
            err_msg = f"getrawmempool answered {txids!r}, not a list of txids"
            raise TypeError(err_msg)
        spent: set[OutPoint] = set()
        for txid in txids:
            tx_hex = self._node.rpc.call("getrawtransaction", [txid])
            if not isinstance(tx_hex, str):
                err_msg = f"getrawtransaction answered {tx_hex!r}, not a hex string"
                raise TypeError(err_msg)
            tx = Tx.parse(bytes.fromhex(tx_hex), check_validity=False)
            spent.update(tx_in.prev_out for tx_in in tx.vin)
        return spent

    def _self_transfer_tx(
        self,
        utxos: Sequence[Utxo],
        amount_per_output: int,
        num_outputs: int,
        *,
        version: int,
        locktime: int,
        sequence: int | Sequence[int],
    ) -> Tx:
        """Return an unpadded, uncached tx spending `utxos` into equal outputs.

        Shared by `create_self_transfer` and `create_self_transfer_multi`,
        which differ only in how they arrive at `amount_per_output`.

        :raises ValueError: `sequence` is a sequence whose length is not
            the number of `utxos` -- `wallet.py`'s own `assert_equal`.
        """
        sequences = [sequence] * len(utxos) if isinstance(sequence, int) else sequence
        if len(sequences) != len(utxos):
            err_msg = f"{len(sequences)} sequence(s) for {len(utxos)} coin(s)"
            raise ValueError(err_msg)
        tx_in = [
            TxIn(
                utxo.outpoint,
                script_sig=b"",
                sequence=utxo_sequence,
                script_witness=_witness(),
            )
            for utxo, utxo_sequence in zip(utxos, sequences, strict=True)
        ]
        tx_out = [
            TxOut(amount_per_output, _ANYONE_CAN_SPEND) for _ in range(num_outputs)
        ]
        return Tx(version=version, lock_time=locktime, vin=tx_in, vout=tx_out)

    def create_self_transfer(
        self,
        *,
        fee_rate: int = DEFAULT_FEE_RATE,
        fee: int = 0,
        utxo_to_spend: Utxo | None = None,
        target_vsize: int = 0,
        confirmed_only: bool = False,
        version: int = 2,
        locktime: int = 0,
        sequence: int | Sequence[int] = 0,
    ) -> Tx:
        """Return an unbroadcast tx spending one coin, paid to itself.

        `create_self_transfer` (`wallet.py`), its fee arithmetic included:
        `fee` where nonzero, otherwise `fee_rate` over the tx's own vsize,
        or over `target_vsize` where that is nonzero, rounded up to the
        next satoshi -- the result `wallet.py` reaches both through
        `get_fee` and through truncating the output's own value. Core's
        own method takes BTC `Decimal`s; this one takes satoshis and
        satoshis per 1000 virtual bytes, the unit `CFeeRate`'s own integer
        constructor takes, so a Core rate of at most eight decimals
        converts exactly. Core prices the fee at a fixed 104 virtual bytes
        for `ADDRESS_OP_TRUE` and asserts the tx has them; this one reads
        the vsize off the tx it built, the same 104 for the one coin shape
        this class spends. `send_self_transfer` is the caller wanting it
        broadcast too, and cached; `new_utxos` is the coin it creates.

        :param fee_rate: satoshis per 1000 virtual bytes, used where `fee`
            is `0`; `DEFAULT_FEE_RATE`, Core's own default, where not given.
        :param fee: the absolute fee in satoshis; `0` defers to `fee_rate`.
        :param utxo_to_spend: the coin to spend, `get_utxo`'s own answer
            typically; `get_utxo()`'s own largest matured coin where
            `None`, its maturity check applying only then -- a caller
            naming one by hand, immature or not, gets exactly that one,
            `create_self_transfer` (`wallet.py`)'s own shape.
        :param target_vsize: where nonzero, an `OP_RETURN` output padding
            the tx to exactly this many virtual bytes -- `_pad_to_vsize`,
            beyond the single spendable output this method still returns
            exactly one of -- and the size `fee_rate` is priced at.
        :param confirmed_only: forwarded to `get_utxo` where
            `utxo_to_spend` is `None`.
        :param version: the tx's own version; `3` makes it TRUC (BIP431).
        :param locktime: the tx's own `nLockTime`.
        :param sequence: the input's own `nSequence`, or a one-element
            sequence of it, as Core's own forwarding to
            `create_self_transfer_multi` takes either.
        :raises LookupError: `utxo_to_spend` is `None` and no coin of this
            wallet has matured yet (or is confirmed, with `confirmed_only`).
        :raises ValueError: `fee_rate` or `fee` is negative, refused
            before any coin is taken; the fee leaves the coin nothing to
            send, Core's own `RuntimeError`; `sequence` is a sequence of
            other than one element; or `target_vsize` is smaller than this
            tx's own vsize before the padding output is even added.
        """
        if fee_rate < 0 or fee < 0:
            err_msg = f"fee_rate {fee_rate} and fee {fee} must not be negative"
            raise ValueError(err_msg)
        utxo = (
            utxo_to_spend
            if utxo_to_spend is not None
            else self.get_utxo(confirmed_only=confirmed_only)
        )
        tx = self._self_transfer_tx(
            [utxo], 0, 1, version=version, locktime=locktime, sequence=sequence
        )
        if not fee:
            # CFeeRate::GetFee: rounded up, never down, to the next satoshi
            fee = -(-fee_rate * (target_vsize or tx.vsize) // 1000)
        if utxo.value <= fee:
            err_msg = f"coin of {utxo.value} sat cannot cover a {fee}-sat fee"
            raise ValueError(err_msg)
        tx.vout[0] = TxOut(utxo.value - fee, _ANYONE_CAN_SPEND)
        if target_vsize:
            _pad_to_vsize(tx, target_vsize)
        return tx

    def _send(self, tx: Tx) -> Tx:
        """Broadcast `tx` over `sendrawtransaction`, cache it, and return it.

        `sendrawtransaction` (`wallet.py`): `maxfeerate` 0, so a
        caller-chosen `fee` or `fee_rate` is never refused for exceeding
        the node's own default ceiling, and `scan_tx` once the node has
        accepted `tx` -- `_scan_tx` here -- so a refused `tx` changes
        nothing in the cache.

        :raises TypeError: `sendrawtransaction` answered something other
            than `tx`'s own id.
        """
        tx_hex = tx.serialize(True, check_validity=False).hex()
        txid = self._node.rpc.call("sendrawtransaction", [tx_hex, 0])
        if txid != tx.id.hex():
            err_msg = f"sendrawtransaction answered {txid!r}, not {tx.id.hex()!r}"
            raise TypeError(err_msg)
        self._scan_tx(tx)
        return tx

    def send_self_transfer(
        self,
        *,
        fee_rate: int = DEFAULT_FEE_RATE,
        fee: int = 0,
        utxo_to_spend: Utxo | None = None,
        target_vsize: int = 0,
        confirmed_only: bool = False,
        version: int = 2,
        locktime: int = 0,
        sequence: int | Sequence[int] = 0,
    ) -> Tx:
        """Create, broadcast and cache a self-transfer; return the sent tx.

        Every parameter is forwarded to `create_self_transfer`, as Core's
        own `send_self_transfer` (`wallet.py`) forwards its `kwargs`, and
        the broadcast passes `maxfeerate` 0, as Core's own does, so no fee
        is refused for exceeding the node's default ceiling. The new coin
        is cached once the node accepts the tx, and is spendable the
        moment this returns: unlike a coinbase, `get_utxo` never holds a
        non-coinbase coin back as immature.

        :param fee_rate: forwarded to `create_self_transfer`.
        :param fee: forwarded to `create_self_transfer`.
        :param utxo_to_spend: forwarded to `create_self_transfer`.
        :param target_vsize: forwarded to `create_self_transfer`.
        :param confirmed_only: forwarded to `create_self_transfer`.
        :param version: forwarded to `create_self_transfer`.
        :param locktime: forwarded to `create_self_transfer`.
        :param sequence: forwarded to `create_self_transfer`.
        :raises LookupError: `utxo_to_spend` is `None` and no coin of this
            wallet has matured yet (or is confirmed, with `confirmed_only`).
        :raises ValueError: forwarded from `create_self_transfer`.
        :raises TypeError: `sendrawtransaction` answered something other
            than the sent tx's own id.
        """
        return self._send(
            self.create_self_transfer(
                fee_rate=fee_rate,
                fee=fee,
                utxo_to_spend=utxo_to_spend,
                target_vsize=target_vsize,
                confirmed_only=confirmed_only,
                version=version,
                locktime=locktime,
                sequence=sequence,
            )
        )

    def create_self_transfer_multi(
        self,
        *,
        utxos_to_spend: Sequence[Utxo] | None = None,
        num_outputs: int = 1,
        version: int = 2,
        locktime: int = 0,
        sequence: int | Sequence[int] = 0,
        fee_per_output: int = FEE,
        target_vsize: int = 0,
        confirmed_only: bool = False,
    ) -> Tx:
        """Return an unbroadcast tx spending several coins into several.

        `create_self_transfer_multi` (`wallet.py`): every input the coins
        `utxos_to_spend` names, every output the same size, `fee_per_output`
        satoshis short of an equal share of the inputs' own total, Core's
        own unit for this one parameter. It takes no `fee_rate`, as Core's
        does not. `send_self_transfer_multi` is the caller wanting it
        broadcast too, and cached; `new_utxos` is the coins it creates. A
        single new coin wants `create_self_transfer`
        instead, `num_outputs` fixed at one there rather than a parameter
        of it.

        :param utxos_to_spend: the coins to spend; `get_utxo()`'s own
            largest matured coin alone where `None`.
        :param num_outputs: how many equal-sized outputs to create.
        :param version: the tx's own version; `3` makes it TRUC (BIP431).
        :param locktime: the tx's own `nLockTime`.
        :param sequence: every input's own `nSequence`, or one per input,
            in the order of `utxos_to_spend`.
        :param fee_per_output: satoshis short of an equal share of the
            inputs' own total value, per output.
        :param target_vsize: where nonzero, an `OP_RETURN` output padding
            the tx to exactly this many virtual bytes, beyond the
            `num_outputs` spendable ones this method still creates.
        :param confirmed_only: forwarded to `get_utxo` where
            `utxos_to_spend` is `None`.
        :raises LookupError: `utxos_to_spend` is `None` and no coin of
            this wallet has matured yet (or is confirmed, with
            `confirmed_only`).
        :raises ValueError: the inputs' own total, less `fee_per_output`
            times `num_outputs`, does not divide into `num_outputs`
            positive shares; `sequence` is a sequence whose length is not
            the number of coins spent; or `target_vsize` is smaller than
            this tx's own vsize before the padding output is even added.
        """
        utxos = (
            list(utxos_to_spend)
            if utxos_to_spend is not None
            else [self.get_utxo(confirmed_only=confirmed_only)]
        )
        inputs_total = sum(utxo.value for utxo in utxos)
        amount_per_output = (inputs_total - fee_per_output * num_outputs) // num_outputs
        if amount_per_output <= 0:
            err_msg = (
                f"{inputs_total} sat across {len(utxos)} coin(s), less "
                f"{fee_per_output} sat fee per output, does not cover "
                f"{num_outputs} output(s)"
            )
            raise ValueError(err_msg)
        tx = self._self_transfer_tx(
            utxos,
            amount_per_output,
            num_outputs,
            version=version,
            locktime=locktime,
            sequence=sequence,
        )
        if target_vsize:
            _pad_to_vsize(tx, target_vsize)
        return tx

    def send_self_transfer_multi(
        self,
        *,
        utxos_to_spend: Sequence[Utxo] | None = None,
        num_outputs: int = 1,
        version: int = 2,
        locktime: int = 0,
        sequence: int | Sequence[int] = 0,
        fee_per_output: int = FEE,
        target_vsize: int = 0,
        confirmed_only: bool = False,
    ) -> Tx:
        """Create, broadcast and cache a multi-output self-transfer.

        The broadcast passes `maxfeerate` 0, as `send_self_transfer`'s does.

        :param utxos_to_spend: forwarded to `create_self_transfer_multi`.
        :param num_outputs: forwarded to `create_self_transfer_multi`.
        :param version: forwarded to `create_self_transfer_multi`.
        :param locktime: forwarded to `create_self_transfer_multi`.
        :param sequence: forwarded to `create_self_transfer_multi`.
        :param fee_per_output: forwarded to `create_self_transfer_multi`.
        :param target_vsize: forwarded to `create_self_transfer_multi`.
        :param confirmed_only: forwarded to `create_self_transfer_multi`.
        :raises LookupError: `utxos_to_spend` is `None` and no coin of
            this wallet has matured yet (or is confirmed, with
            `confirmed_only`).
        :raises ValueError: forwarded from `create_self_transfer_multi`.
        :raises TypeError: `sendrawtransaction` answered something other
            than the sent tx's own id.
        """
        return self._send(
            self.create_self_transfer_multi(
                utxos_to_spend=utxos_to_spend,
                num_outputs=num_outputs,
                version=version,
                locktime=locktime,
                sequence=sequence,
                fee_per_output=fee_per_output,
                target_vsize=target_vsize,
                confirmed_only=confirmed_only,
            )
        )

    def create_self_transfer_chain(
        self, *, chain_length: int, utxo_to_spend: Utxo | None = None
    ) -> list[Tx]:
        """Return `chain_length` unbroadcast txs, each spending the last.

        `create_self_transfer_chain` (`wallet.py`): the first spends
        `utxo_to_spend`, or `get_utxo()`'s own largest matured coin where
        `None`; each of the rest spends the single output the one before
        it created, `new_utxos`' own answer, as Core's spends `new_utxo`.
        `send_self_transfer_chain` is the caller wanting every one of them
        broadcast too, and cached.

        :param chain_length: how many transactions the chain carries.
        :param utxo_to_spend: the coin the first transaction spends;
            `get_utxo()`'s own largest matured coin where `None`.
        :raises LookupError: `utxo_to_spend` is `None` and no coin of
            this wallet has matured yet.
        """
        chain = []
        utxo = utxo_to_spend
        for _ in range(chain_length):
            tx = self.create_self_transfer(utxo_to_spend=utxo)
            chain.append(tx)
            utxo = self.new_utxos(tx)[0]
        return chain

    def send_self_transfer_chain(
        self, *, chain_length: int, utxo_to_spend: Utxo | None = None
    ) -> list[Tx]:
        """Create, broadcast and cache a chain of self-transfers.

        Each broadcast caches its own transaction's output and drops the
        one before it, which that transaction spends, so the last
        transaction's own output is the one the chain leaves cached.

        :param chain_length: forwarded to `create_self_transfer_chain`.
        :param utxo_to_spend: forwarded to `create_self_transfer_chain`.
        :raises LookupError: `utxo_to_spend` is `None` and no coin of
            this wallet has matured yet.
        :raises TypeError: a `sendrawtransaction` answered something
            other than its own tx's id.
        """
        chain = self.create_self_transfer_chain(
            chain_length=chain_length, utxo_to_spend=utxo_to_spend
        )
        for tx in chain:
            self._send(tx)
        return chain

    def send_to(self, script_pub_key: ScriptPubKey, value: int) -> Tx:
        """Spend one matured coin, broadcast, paying `script_pub_key` too.

        `send_to` (`wallet.py`): a second output pays `script_pub_key`,
        and the first keeps `FEE` sat back for the fee and returns the
        rest to this wallet as change, so the spent coin's own value
        beyond `value` and `FEE` is not simply burned as a fee the way it
        would be paying a single output alone. The coin spent is
        `get_utxo()`'s own, the largest matured one, and the tx around it
        is the one `create_self_transfer(fee_rate=0)` builds -- version 2,
        `nLockTime` and `nSequence` 0 -- as `wallet.py`'s own `send_to`
        takes both.

        :param script_pub_key: what the new, second output pays.
        :param value: the new output's own satoshi value.
        :raises LookupError: no coin of this wallet has matured yet.
        :raises ValueError: the spent coin cannot cover `value` and this
            class's own `FEE` together.
        :raises TypeError: `sendrawtransaction` answered something other
            than the sent tx's own id.
        """
        utxo = self.get_utxo()
        if utxo.value < value + FEE:
            err_msg = (
                f"coin of {utxo.value} sat cannot cover {value} sat plus "
                f"this class's own {FEE}-sat fee"
            )
            raise ValueError(err_msg)
        change = utxo.value - value - FEE
        tx = self._self_transfer_tx(
            [utxo], change, 1, version=2, locktime=0, sequence=0
        )
        tx.vout.append(TxOut(value, script_pub_key))
        return self._send(tx)
