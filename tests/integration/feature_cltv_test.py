# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_cltv`, one body over each node.

Read from Core's `test/functional/feature_cltv.py` (`fa6f7e36a8c2`,
2026-10-23), on the option and MiniWallet families together
([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)):
`-testactivationheight=cltv@N` (`Capability.TEST_ACTIVATION_HEIGHT`)
holds BIP65 inactive until a chosen height, and blocks built and
submitted here (`Capability.MINE`) reach it.

Kept: `getdeploymentinfo`'s own `bip65` entry, transitioning one block
before the configured height, and the buried-deployment version floor a
version-3 block trips once BIP65 is active -- `bad-version(0x00000003)`,
`submitblock`'s own answer and, in a body of its own, bitcoind's own
debug log line. That check is isolated from the other buried
deployments' own default floors, unlike `feature_dersig`'s: a version-3
block already satisfies BIP66's own default floor (active from height 1
regardless of `-testactivationheight=cltv@N`), so version 3 is refused
only once the configured height is reached, and Core's own file checks
exactly this version.

BIP65's failure reasons are kept too
([ISS 167](https://github.com/btclib-org/functional-tests/issues/167)).
They need no signature: Core's coins are its `RAW_OP_TRUE` ones,
coinbases paying a bare `OP_TRUE` and spent with a scriptSig of
`OP_NOP`s alone, and `_cltv_invalidate` prepends to that scriptSig what
fails `OP_CHECKLOCKTIMEVERIFY` for one reason, `_cltv_validate` what
passes it. Each body asks only for what it needs:

- before the configured height, a block carrying the invalid spends
  is accepted, and at it a block carrying any of them is refused in
  Core's wording, at Core's own height so that the coinbases have
  matured -- the boundary where the script rule starts;
- once BIP65 is active, `testmempoolaccept` refuses each for its own
  reason, in Core's wording. Without Core's own `-acceptnonstdtxn=1`
  (`Capability.ACCEPT_NON_STANDARD`) bitcoind refuses each as
  `scriptsig-not-pushonly` before any script runs, so the node is
  restarted with it;
- once BIP65 is active, `submitblock` refuses a block carrying each, in
  Core's wording, and accepts one carrying the spend CLTV admits. Core
  reads that refusal from the debug log; `submitblock`'s answer carries
  the same reason.

The mempool and block refusals run on regtest's own default, BIP65
active from height 1 (`Capability.TEST_ACTIVATION_HEIGHT`'s own
docstring), where Core runs them at its configured height: neither asks
for `-testactivationheight`, so neither is gated on it, and the first
body is what pins the height the rule starts at.

Each `feature_cltv_<node>_test.py` module beside this one runs these,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from btclib.script.script import serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY, SEQUENCE_FINAL

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.mini_wallet import (
    FEE,
    MiniWallet,
    build_fork,
    build_next_block,
)
from tests.integration.support.script_verify_flag import block_script_verify_flag_failed

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.block.block import Block

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_version_3_block_is_logged_once_active",
    "a_version_3_block_is_refused_once_active",
    "cltv_activates_one_block_before_the_configured_height",
    "cltv_failures_are_mined_until_the_configured_height",
    "cltv_failures_are_refused_by_the_mempool",
    "cltv_failures_are_refused_in_a_block",
]

# Core's own file hardcodes 111; the bodies using this need only that the
# height is reached in a handful of mined blocks
_CLTV_HEIGHT = 12

# Core's own height, which the spends before activation need: the coinbases
# they spend matured by the block before it
_CORE_CLTV_HEIGHT = 111

# Core's `RAW_OP_TRUE` scriptPubKey, a bare `OP_TRUE`
_RAW_OP_TRUE = ScriptPubKey(b"\x51", check_validity=False)

# the scriptSig Core's `MiniWallet.sign_tx` gives a `RAW_OP_TRUE` spend,
# padding it to a signed spend's size
_RAW_OP_TRUE_SCRIPT_SIG = serialize(["OP_NOP"] * 43)

# `TIME_GENESIS_BLOCK` (`test_framework/blocktools.py`), a timestamp and so
# the other lock-time type from a block height
_TIME_GENESIS_BLOCK = 1296688602

# Core's own `cltv_invalidate` table: what to prepend, and the nSequence and
# nLockTime to set where not None
_INVALIDATE: tuple[tuple[list[int | str], int | None, int | None], ...] = (
    (["OP_CHECKLOCKTIMEVERIFY"], None, None),
    (["OP_1NEGATE", "OP_CHECKLOCKTIMEVERIFY", "OP_DROP"], None, None),
    ([100, "OP_CHECKLOCKTIMEVERIFY", "OP_DROP"], 0, _TIME_GENESIS_BLOCK),
    ([100, "OP_CHECKLOCKTIMEVERIFY", "OP_DROP"], 0, 50),
    ([50, "OP_CHECKLOCKTIMEVERIFY", "OP_DROP"], SEQUENCE_FINAL, 50),
)

# the reason bitcoind names for each row of `_INVALIDATE`, after the
# `*-script-verify-flag-failed` prefix
_REASONS = (
    " (Operation not valid with the current stack size)",
    " (Negative locktime)",
    " (Locktime requirement not satisfied)",
    " (Locktime requirement not satisfied)",
    " (Locktime requirement not satisfied)",
)


def _block(
    node: NodeAdapter,
    *,
    version: int = 4,
    transactions: Sequence[Tx] = (),
) -> Block:
    """Return `build_next_block`'s own block, paying `_RAW_OP_TRUE`.

    :param node: the node whose own tip this block extends.
    :param version: the block header's own version; `4`, Core's own
        `create_block` default, where not given.
    :param transactions: what the block carries beside its coinbase.
    """
    return build_next_block(node, _RAW_OP_TRUE, transactions, version=version)


def _submit(node: NodeAdapter, block: Block) -> object:
    """Return `submitblock`'s own answer for `block`."""
    return node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])


def _mine_coins(node: NodeAdapter, count: int) -> list[Tx]:
    """Mine `count` blocks paying `_RAW_OP_TRUE`, and return their coinbases."""
    coinbases = []
    for block in build_fork(node, _RAW_OP_TRUE, count):
        assert _submit(node, block) is None
        coinbases.append(block.transactions[0])
    return coinbases


def _spend(
    coinbase: Tx, prepend: Sequence[int | str], sequence: int, lock_time: int
) -> Tx:
    """Return a spend of `coinbase`, `prepend` ahead of its own scriptSig.

    Core's `create_self_transfer` in `RAW_OP_TRUE` mode, then
    `cltv_modify_tx`.
    """
    tx_in = TxIn(
        OutPoint(coinbase.id, 0),
        script_sig=serialize(list(prepend)) + _RAW_OP_TRUE_SCRIPT_SIG,
        sequence=sequence,
        check_validity=False,
    )
    tx_out = TxOut(coinbase.vout[0].value - FEE, _RAW_OP_TRUE, check_validity=False)
    return Tx(
        version=2, lock_time=lock_time, vin=[tx_in], vout=[tx_out], check_validity=False
    )


def _cltv_invalidate(coinbase: Tx, failure_reason: int) -> Tx:
    """Return a spend of `coinbase` failing CLTV for `failure_reason`.

    Core's own `cltv_invalidate`, one row of `_INVALIDATE`; nSequence and
    nLockTime otherwise keep `create_self_transfer`'s own zeros.
    """
    prepend, sequence, lock_time = _INVALIDATE[failure_reason]
    return _spend(
        coinbase,
        prepend,
        0 if sequence is None else sequence,
        0 if lock_time is None else lock_time,
    )


def _cltv_validate(spend: Tx, height: int) -> Tx:
    """Return `spend` with a CLTV for `height` prepended, and satisfied.

    Core's own `cltv_validate`: nLockTime `height`, nSequence `0`, the
    earlier prepend kept after the new one.
    """
    (tx_in,) = spend.vin
    return Tx(
        version=spend.version,
        lock_time=height,
        vin=[
            TxIn(
                tx_in.prev_out,
                script_sig=serialize([height, "OP_CHECKLOCKTIMEVERIFY", "OP_DROP"])
                + tx_in.script_sig,
                sequence=0,
                check_validity=False,
            )
        ],
        vout=spend.vout,
        check_validity=False,
    )


def _bip65(node: NodeAdapter) -> dict[str, object]:
    """Return `getdeploymentinfo`'s own `bip65` entry."""
    bip65: dict[str, object] = node.rpc.call("getdeploymentinfo")["deployments"][
        "bip65"
    ]
    return bip65


def _start_with_cltv_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    height: int,
) -> NodeAdapter:
    """Return one node, restarted with BIP65 held back until `height`."""
    (node,) = cluster(1)
    require(Capability.TEST_ACTIVATION_HEIGHT, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart([f"-testactivationheight=cltv@{height}"])
    return node


def cltv_activates_one_block_before_the_configured_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `getdeploymentinfo`'s `bip65` entry tracks the configured height.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_cltv_height(cluster, skip_counts, _CLTV_HEIGHT)
    wallet = MiniWallet(node)
    assert _bip65(node) == {"type": "buried", "active": False, "height": _CLTV_HEIGHT}

    wallet.generate(_CLTV_HEIGHT - 2)
    assert _bip65(node)["active"] is False

    wallet.generate(1)  # tip is now one block before the configured height
    assert _bip65(node)["active"] is True


def a_version_3_block_is_refused_once_active(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check that once BIP65 is active, a version-3 block never becomes the tip.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_cltv_height(cluster, skip_counts, _CLTV_HEIGHT)
    MiniWallet(node).generate(_CLTV_HEIGHT - 1)
    old_tip = node.rpc.call("getbestblockhash")

    assert _submit(node, _block(node, version=3)) == "bad-version(0x00000003)"
    assert node.rpc.call("getbestblockhash") == old_tip


def a_version_3_block_is_logged_once_active(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check the same refusal, in bitcoind's own debug log wording.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    :raises TypeError: the node declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    node = _start_with_cltv_height(cluster, skip_counts, _CLTV_HEIGHT)
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    MiniWallet(node).generate(_CLTV_HEIGHT - 1)

    block = _block(node, version=3)
    with assert_debug_log(node.debug_log_path, ["bad-version(0x00000003)"]):
        _submit(node, block)


def cltv_failures_are_mined_until_the_configured_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check CLTV failures are mined before the configured height, not at it.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_cltv_height(cluster, skip_counts, _CORE_CLTV_HEIGHT)
    coinbases = _mine_coins(node, _CORE_CLTV_HEIGHT - 2)
    spends = [_cltv_invalidate(coinbases[i], i) for i in range(len(_INVALIDATE))]

    # not active as of the tip, and the next block does not obey its rules
    assert _bip65(node)["active"] is False
    block = _block(node, version=3, transactions=spends)
    assert _submit(node, block) is None
    assert node.rpc.call("getbestblockhash") == block.header.hash.hex()
    # not active as of the block before, but the next block must obey them
    assert _bip65(node)["active"] is True
    tip = block.header.hash.hex()

    # the block at the configured height refuses each, coins not yet spent
    for i, reason in enumerate(_REASONS):
        spend = _cltv_invalidate(coinbases[len(_INVALIDATE) + i], i)
        answer = _submit(node, _block(node, transactions=[spend]))
        assert answer == f"{block_script_verify_flag_failed(node)}{reason}"
        assert node.rpc.call("getbestblockhash") == tip


def cltv_failures_are_refused_by_the_mempool(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `testmempoolaccept` refuses each CLTV failure for its own reason.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.ACCEPT_NON_STANDARD, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart(["-acceptnonstdtxn=1"])
    coinbases = _mine_coins(node, COINBASE_MATURITY + len(_INVALIDATE))

    for i, reason in enumerate(_REASONS):
        spend = _cltv_invalidate(coinbases[i], i)
        txid = spend.id.hex()
        reject_reason = f"mempool-script-verify-flag-failed{reason}"
        answer = node.rpc.call(
            "testmempoolaccept",
            [[spend.serialize(False, check_validity=False).hex()], 0],
        )
        assert answer == [
            {
                "txid": txid,
                "wtxid": txid,
                "allowed": False,
                "reject-reason": reject_reason,
                "reject-details": (
                    f"{reject_reason}, input 0 of {txid} (wtxid {txid}), "
                    f"spending {coinbases[i].id.hex()}:0"
                ),
            }
        ]


def cltv_failures_are_refused_in_a_block(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check a block carrying a CLTV failure is refused, a valid spend not.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.MINE, node.capabilities, skip_counts)
    coinbases = _mine_coins(node, COINBASE_MATURITY + len(_INVALIDATE))
    tip = node.rpc.call("getbestblockhash")

    spend = None
    for i, reason in enumerate(_REASONS):
        spend = _cltv_invalidate(coinbases[i], i)
        answer = _submit(node, _block(node, transactions=[spend]))
        assert answer == f"{block_script_verify_flag_failed(node)}{reason}"
        assert node.rpc.call("getbestblockhash") == tip

    # the last one again, with a CLTV its own nLockTime satisfies prepended
    assert spend is not None
    height = node.rpc.call("getblockcount")
    block = _block(node, transactions=[_cltv_validate(spend, height)])
    assert _submit(node, block) is None
    assert node.rpc.call("getbestblockhash") == block.header.hash.hex()
