# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_assumevalid`, one body over each node.

Read from Core's `test/functional/feature_assumevalid.py` (`fa16bc53d79c`,
2026-04-16), an option and the log
([ISS 14](https://github.com/btclib-org/functional-tests/issues/14)): a
chain carries a coinbase output spent, once it matures, by a transaction
with an empty (invalid) signature, and that spend is buried under a
further stretch of blocks reaching past two weeks' worth of work.
`-assumevalid=<hash>`, named at the spending block (`Capability.ASSUME_VALID`),
lets a node skip script verification under that block, so the spend's own
invalid signature is never checked -- the log names which block starts and
which ends the skip, and why (`Capability.DEBUG_LOG`).

Six nodes, each fresh:

- the first, given no `-assumevalid`, verifies every block: the spend is
  caught, the peer sending it is dropped, and the chain's tip past the
  spend is reported invalid (`Capability.CHAIN_TIPS`);
- the second, given the option, skips verification up to and including
  the spending block and takes the whole chain;
- the third, given the option but fed only part of the chain's headers,
  is not yet buried deep enough for the skip and rejects the spend the
  same way the first node does;
- the fourth is fed a competing, higher-work header chain from genesis
  ahead of the real one, so the real chain's first block is not on its
  best header chain, is verified in full, and is accepted regardless,
  carrying no spend of its own;
- the fifth is fed a block at the same height outside the assumevalid
  chain entirely, verified in full and accepted the same way;
- the sixth is restarted under `-reindex-chainstate`, once naming a hash
  not among its headers and once naming the real hash under a chain-work
  floor it cannot meet, hitting each gate without a header-download race.

What differs from Core's file:

- every node starts without its own option, the four that need
  `-assumevalid` restarted with it once the chain's own hash is known,
  as other ports of this harness restart a node rather than starting it
  configured from the first start;
- each block is built with btclib in the shape of Core's own
  `create_block` and `create_coinbase` (`blocktools.py`); the first
  block's coinbase pays a fixed private key's P2PK output rather than a
  fresh one Core's own `generate_keypair` draws each run, and the spend
  is Core's own `create_tx_with_script(block1.vtx[0], 0, script_sig=b"",
  amount=49 * COIN)`;
- block timestamps increase by one second throughout, where Core's own
  file increases its own counter twice in a row once, around the
  spending block -- harmless to a chain that only needs strictly
  increasing timestamps;
- Core's own `p2p0`/`p2p1`/... are named after the node each is
  connected to instead;
- the log lines a node's own action is checked against are read
  per-build off `getnetworkinfo`'s `version`, through constants each
  naming its own merge and release: a script-verification toggle logs
  nothing at all before `v30.0`, logs the older wording from there
  through `v30.x`, and from `v31.0` on names the reason too
  (`bitcoin/bitcoin#32975`, then `bitcoin/bitcoin#33336`); the
  rejection's own line names it `block-script-verify-flag-failed` only
  from `v30.0` on, `mandatory-script-verify-flag-failed` below that
  (`bitcoin/bitcoin#33183`) -- a rename unrelated to the other two.
  Every acceptance, rejection and disconnect this test checks for still
  holds on a `v29.4` or a `v30.3` build, only the log read against it
  differs, or is left unread where the build logs none.

Each `feature_assumevalid_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from contextlib import ExitStack, nullcontext
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.block.block import Block, bip34_commitment
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS
from btclib.key import PrvKeyData
from btclib.p2p import BlockPayload, Headers
from btclib.script.script import serialize
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY, SEQUENCE_FINAL

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Peer
from tests.integration.support.script_verify_flag import (
    bitcoind_version,
    block_script_verify_flag_failed,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "TEST_TIMEOUT",
    "script_verification_depends_on_assumevalid_and_its_conditions",
]

type _Node = NodeAdapter

_MAGIC = magic_from_chain("regtest")

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

# Core's own default coinbase output, an anyone-can-spend `OP_TRUE`
_OP_TRUE = b"\x51"

# a fixed private key standing in for Core's own `generate_keypair()`:
# the coinbase this pays is spent with an empty `script_sig`, so what is
# under test is that its script runs, not the key
_P2PK_SCRIPT = serialize([PrvKeyData(1, "regtest").pub.sec, "OP_CHECKSIG"])

_COIN = 100_000_000
_SPEND_AMOUNT = 49 * _COIN

# how many blocks bury the spendable coinbase before it is spent, Core's
# own `COINBASE_MATURITY`
_BURY_DEPTH = COINBASE_MATURITY

# how many blocks bury the spending block after it, Core's own comment:
# "just over two weeks' worth of blocks"
_ASSUMED_VALID_BURY_DEPTH = 2100

# how many headers the competing, higher-work chain carries
_COMPETING_CHAIN_LENGTH = 150

# Core's own hash naming no header the node holds
_UNKNOWN_HASH = "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"

# Core's own floor no chain this short reaches
_TOO_HIGH_CHAIN_WORK = "-minimumchainwork=0xffff"

# Core's own `p2p1.sync_with_ping(timeout=960)`, syncing the whole chain
_FULL_SYNC_TIMEOUT = 960.0

# the test's own bound, through `scaled_timeout`: `_FULL_SYNC_TIMEOUT` is at or
# past `pyproject.toml`'s `timeout`, so the test gets that wait plus the
# ordinary `timeout` for the rest of what it does
TEST_TIMEOUT = _FULL_SYNC_TIMEOUT + 300.0

# the `CLIENT_VERSION` of `v30.0`, the first release logging a script
# verification toggle at all (bitcoin/bitcoin#32975); a build before it,
# `v29.4` among them, leaves the toggle unlogged, as Core's own file
# does there too
_SCRIPT_CHECK_TOGGLE_LOGGED_VERSION = 300000

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v31.0`, the first
# release naming why script verification toggled rather than only that
# it did (bitcoin/bitcoin#33336)
_SCRIPT_VERIFICATION_REASON_VERSION = 310000


def _block(
    previous: bytes,
    height: int,
    block_time: int,
    transactions: Sequence[Tx] = (),
    *,
    coinbase_script: bytes = _OP_TRUE,
) -> Block:
    """Core's own `create_block(previous, height=, ntime=, txlist=)`, solved."""
    coinbase = build_coinbase(
        height, coinbase_script, halving_interval=_HALVING_INTERVAL
    )
    candidate = build_block(
        previous,
        [coinbase, *transactions],
        datetime.fromtimestamp(block_time, UTC),
        REGTEST_POW_LIMIT_BITS,
    )
    solved = mine(candidate.header)
    assert solved is not None
    return Block(solved, candidate.transactions, check_validity=False)


def _invalid_spend(coinbase_tx: Tx) -> Tx:
    """Core's own `create_tx_with_script(coinbase_tx, 0, b"", amount=49*COIN)`.

    An empty `script_sig` spending a P2PK output: `OP_CHECKSIG` finds
    only the pubkey on the stack, which fails rather than merely
    refusing an invalid signature -- `_P2PK_SCRIPT`'s own comment has
    why that is still what this test needs.
    """
    tx_in = TxIn(OutPoint(coinbase_tx.id, 0), b"", SEQUENCE_FINAL)
    tx_out = TxOut(_SPEND_AMOUNT, _OP_TRUE)
    return Tx(2, 0, [tx_in], [tx_out], check_validity=False)


def _headers(*blocks: Block) -> Headers:
    """Core's own `msg_headers` over `CBlockHeader(block)` for each block."""
    return Headers([block.header for block in blocks], check_validity=False)


def _block_payload(block: Block) -> BlockPayload:
    """Core's own `msg_block(block)`."""
    return BlockPayload(block, include_witness=True, check_validity=False)


def _connected_peer(stack: ExitStack, node: NodeAdapter) -> Peer:
    """Core's own `add_p2p_connection`: a peer past its handshake.

    Entered into `stack`, which closes it on an exception; the caller
    still closes it once its checks are done.
    """
    peer = stack.enter_context(Peer(node.p2p_address, _MAGIC))
    peer.handshake()
    peer.sync_with_ping()
    return peer


def _send_and_ping(peer: Peer, block: Block) -> None:
    """Core's own `send_and_ping(msg_block(block))`."""
    peer.send(_block_payload(block), check_validity=False)
    peer.sync_with_ping()


def _debug_log(node: _Node) -> Path:
    """Return the log a check reads, `Capability.DEBUG_LOG`'s fact.

    :raises TypeError: `node` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return node.debug_log_path


def _logs_script_check_toggle(node: NodeAdapter) -> bool:
    """Whether `node` logs a script-verification toggle at all.

    Bitcoind before `_SCRIPT_CHECK_TOGGLE_LOGGED_VERSION` excepted.
    """
    version = bitcoind_version(node)
    return version is None or version >= _SCRIPT_CHECK_TOGGLE_LOGGED_VERSION


def _names_script_verification_reason(node: NodeAdapter) -> bool:
    """Whether `node` names why, not only that, script verification toggled.

    Bitcoind before `_SCRIPT_VERIFICATION_REASON_VERSION` excepted.
    """
    version = bitcoind_version(node)
    return version is None or version >= _SCRIPT_VERIFICATION_REASON_VERSION


def _invalid_script_message(node: NodeAdapter) -> str:
    """Return the rejection line the spend's own script failure logs."""
    return f"Block validation error: {block_script_verify_flag_failed(node)}"


def _maybe_assert_debug_log(
    node: _Node, *, condition: bool, expected_substrings: Sequence[str]
) -> AbstractContextManager[None]:
    """`assert_debug_log` over `node`'s log where `condition`, else nothing.

    A node whose script-verification state never toggles logs nothing
    about it on any build carrying the toggle log at all: Core's own
    member tracking the last state logged (`m_prev_script_checks_logged`
    at `v30.x`, `m_last_script_check_reason_logged` from `v31.0`) only
    logs again on a change from it. So the caller's own action still
    needs to run where `condition` is false -- only the log check is
    skipped.
    """
    if not condition:
        return nullcontext()
    return assert_debug_log(_debug_log(node), expected_substrings)


def _block_count(node: NodeAdapter) -> int:
    """Return `getblockcount`'s own answer."""
    count = node.rpc.call("getblockcount")
    assert isinstance(count, int)
    return count


def _chain_tip_status(node: NodeAdapter, block: Block) -> str | None:
    """Return the `getchaintips` status of `block`, `None` where no tip."""
    tips = node.rpc.call("getchaintips")
    assert isinstance(tips, list)
    for tip in tips:
        if tip["hash"] == block.header.hash.hex():
            status = tip["status"]
            assert isinstance(status, str)
            return status
    return None


def _best_block_hash(node: NodeAdapter) -> bytes:
    """Return `getbestblockhash`'s own answer, as the bytes a block wants."""
    block_hash = node.rpc.call("getbestblockhash")
    assert isinstance(block_hash, str)
    return bytes.fromhex(block_hash)


def _best_block_time(node: NodeAdapter) -> int:
    """Return the current tip's own `time`, Core's `getblock(...)['time']`."""
    answer = node.rpc.call("getblock", [node.rpc.call("getbestblockhash")])
    assert isinstance(answer, dict)
    time = answer["time"]
    assert isinstance(time, int)
    return time


def script_verification_depends_on_assumevalid_and_its_conditions(
    cluster: Callable[[int], Sequence[_Node]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `setup_network` and `run_test`, one node per case.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    with ExitStack() as stack:
        node0, node1, node2, node3, node4, node5 = cluster(6)
        for node in (node1, node2, node3, node4, node5):
            require(Capability.ASSUME_VALID, node.capabilities, skip_counts)
        for node in (node0, node1, node2, node3, node4, node5):
            require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
        require(Capability.CHAIN_TIPS, node0.capabilities, skip_counts)
        require(Capability.CHAIN_TIPS, node2.capabilities, skip_counts)
        require(Capability.MINIMUM_CHAIN_WORK, node5.capabilities, skip_counts)
        require(Capability.REINDEX, node5.capabilities, skip_counts)

        # Core's own chain: a spendable coinbase at height 1, buried a
        # maturity period deep, a block spending it with an invalid
        # signature, and that block buried under a further stretch reaching
        # past two weeks' worth of work
        genesis = _best_block_hash(node0)
        block_time = _best_block_time(node0) + 1

        block1 = _block(genesis, 1, block_time, coinbase_script=_P2PK_SCRIPT)
        chain = [block1]
        tip = block1.header.hash
        block_time += 1
        height = 2
        for _ in range(_BURY_DEPTH):
            block = _block(tip, height, block_time)
            chain.append(block)
            tip = block.header.hash
            block_time += 1
            height += 1

        spend = _invalid_spend(block1.transactions[0])
        block102 = _block(tip, height, block_time, [spend])
        chain.append(block102)
        tip = block102.header.hash
        block_time += 1
        height += 1

        for _ in range(_ASSUMED_VALID_BURY_DEPTH):
            block = _block(tip, height, block_time)
            chain.append(block)
            tip = block.header.hash
            block_time += 1
            height += 1

        block1_hash = block1.header.hash.hex()
        assumevalid_arg = f"-assumevalid={block102.header.hash.hex()}"

        node1.restart([assumevalid_arg])
        node2.restart([assumevalid_arg])
        node3.restart([assumevalid_arg])
        node4.restart([assumevalid_arg])

        # node0: no -assumevalid, every block verified in full -- the spend's
        # invalid signature is caught, the peer sending it dropped, and the
        # tip past it reported invalid
        peer0 = _connected_peer(stack, node0)
        peer0.send(_headers(*chain[:2000]), check_validity=False)
        peer0.send(_headers(*chain[2000:]), check_validity=False)
        always_verify = (
            f"Enabling script verification at block #1 ({block1_hash}): "
            "assumevalid=0 (always verify)."
        )
        with _maybe_assert_debug_log(
            node0,
            condition=_names_script_verification_reason(node0),
            expected_substrings=[always_verify],
        ):
            _send_and_ping(peer0, chain[0])
        with assert_debug_log(_debug_log(node0), [_invalid_script_message(node0)]):
            for block in chain[1:103]:
                peer0.send(_block_payload(block), check_validity=False)
            peer0.wait_for_disconnect()
        assert _block_count(node0) == COINBASE_MATURITY + 1
        assert _chain_tip_status(node0, chain[-1]) == "invalid"
        peer0.close()

        # node1: -assumevalid, verification skipped up to and including the
        # spending block -- the whole chain syncs
        peer1 = _connected_peer(stack, node1)
        peer1.send(_headers(*chain[:2000]), check_validity=False)
        peer1.send(_headers(*chain[2000:]), check_validity=False)
        node1_names_reason = _names_script_verification_reason(node1)
        node1_logs_toggle = node1_names_reason or _logs_script_check_toggle(node1)
        disabled = (
            f"Disabling script verification at block #1 ({block1_hash})."
            if node1_names_reason
            else "Disabling signature validations at block #1"
        )
        with _maybe_assert_debug_log(
            node1, condition=node1_logs_toggle, expected_substrings=[disabled]
        ):
            _send_and_ping(peer1, chain[0])
        enabled_past_spend = (
            (
                f"Enabling script verification at block #{_BURY_DEPTH + 3} "
                f"({chain[_BURY_DEPTH + 2].header.hash.hex()}): "
                "block height above assumevalid height."
            )
            if node1_names_reason
            else f"Enabling signature validations at block #{_BURY_DEPTH + 3}"
        )
        with _maybe_assert_debug_log(
            node1, condition=node1_logs_toggle, expected_substrings=[enabled_past_spend]
        ):
            for block in chain[1:]:
                peer1.send(_block_payload(block), check_validity=False)
            peer1.sync_with_ping(timeout=_FULL_SYNC_TIMEOUT)
        assert _block_count(node1) == len(chain)
        peer1.close()

        # node2: -assumevalid, but only part of the chain's headers -- not
        # buried deep enough yet, so the spend is still caught
        peer2 = _connected_peer(stack, node2)
        peer2.send(_headers(*chain[:200]), check_validity=False)
        too_recent = (
            f"Enabling script verification at block #1 ({block1_hash}): "
            "block too recent relative to best header."
        )
        with _maybe_assert_debug_log(
            node2,
            condition=_names_script_verification_reason(node2),
            expected_substrings=[too_recent],
        ):
            _send_and_ping(peer2, chain[0])
        with assert_debug_log(_debug_log(node2), [_invalid_script_message(node2)]):
            for block in chain[1:103]:
                peer2.send(_block_payload(block), check_validity=False)
            peer2.wait_for_disconnect()
        assert _block_count(node2) == COINBASE_MATURITY + 1
        assert _chain_tip_status(node2, chain[199]) == "invalid"
        peer2.close()

        # node3: a competing, higher-work header chain from genesis leaves
        # the real chain's first block off the best header chain -- verified
        # in full, and accepted, carrying no spend of its own
        competing_tip = _best_block_hash(node3)
        competing_time = _best_block_time(node3) + 1
        competing_chain = []
        for offset in range(_COMPETING_CHAIN_LENGTH):
            block = _block(competing_tip, offset + 1, competing_time)
            competing_chain.append(block)
            competing_tip = block.header.hash
            competing_time += 1
        # Core's own `second_chain_height` starts at `tip_block["height"] + 1`,
        # the genesis tip's own height 0 plus 1 -- BIP34 commits that height
        # into the coinbase, so a height off by one commits a different byte
        coinbase_script_sig = competing_chain[0].transactions[0].vin[0].script_sig
        assert coinbase_script_sig.startswith(bip34_commitment(1))
        peer3 = _connected_peer(stack, node3)
        peer3.send(_headers(*competing_chain), check_validity=False)
        peer3.send(_headers(*chain[:103]), check_validity=False)
        not_best_header_chain = (
            f"Enabling script verification at block #1 ({block1_hash}): "
            "block not in best header chain."
        )
        with _maybe_assert_debug_log(
            node3,
            condition=_names_script_verification_reason(node3),
            expected_substrings=[not_best_header_chain],
        ):
            _send_and_ping(peer3, chain[0])
        assert _block_count(node3) == 1
        peer3.close()

        # node4: a block at height 1 outside the assumevalid chain entirely
        # -- verified in full, and accepted
        alt1 = _block(_best_block_hash(node4), 1, _best_block_time(node4) + 2)
        peer4 = _connected_peer(stack, node4)
        peer4.send(_headers(*chain[:103]), check_validity=False)
        not_in_assumevalid_chain = (
            f"Enabling script verification at block #1 ({alt1.header.hash.hex()}): "
            "block not in assumevalid chain."
        )
        with _maybe_assert_debug_log(
            node4,
            condition=_names_script_verification_reason(node4),
            expected_substrings=[not_in_assumevalid_chain],
        ):
            _send_and_ping(peer4, alt1)
        assert _block_count(node4) == 1
        peer4.close()

        # node5: reindexing hits the assumevalid gates directly, without a
        # header-download race -- the hash not yet known, then known but not
        # buried behind enough chain work
        peer5 = _connected_peer(stack, node5)
        peer5.send(_headers(*chain[:200]), check_validity=False)
        peer5.send(_block_payload(chain[0]), check_validity=False)
        wait_until(lambda: _block_count(node5) == 1)
        peer5.close()
        node5_names_reason = _names_script_verification_reason(node5)
        hash_not_in_headers = (
            f"Enabling script verification at block #1 ({block1_hash}): "
            "assumevalid hash not in headers."
        )
        with _maybe_assert_debug_log(
            node5,
            condition=node5_names_reason,
            expected_substrings=[hash_not_in_headers],
        ):
            node5.restart(["-reindex-chainstate", f"-assumevalid={_UNKNOWN_HASH}"])
        assert _block_count(node5) == 1
        below_minimum_chain_work = (
            f"Enabling script verification at block #1 ({block1_hash}): "
            "best header chainwork below minimumchainwork."
        )
        with _maybe_assert_debug_log(
            node5,
            condition=node5_names_reason,
            expected_substrings=[below_minimum_chain_work],
        ):
            node5.restart(
                ["-reindex-chainstate", assumevalid_arg, _TOO_HIGH_CHAIN_WORK]
            )
        assert _block_count(node5) == 1
