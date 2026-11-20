# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_block`, as bodies over each node.

Read from Core's `test/functional/feature_block.py`: a node is fed a
sequence of valid and invalid blocks, forks and re-orgs, over one peer,
and each block's verdict is checked. `btclib_functional_tests.feature_block`
builds that sequence as data, and its module docstring says where the
sequence differs from Core's file. Each body here feeds the whole
sequence to a fresh node over p2p, the way Core's `P2PDataStore` does.

A block is announced by its header, and the peer answers each `getdata`
with the blocks it has sent on that connection, until one names the
block announced; a block the node has no reason to ask for is sent
unasked. A batch is announced by its blocks' headers in one message, and
answered until a `getdata` names its last block. Then the body waits for
the node to drop the peer where Core does, and otherwise for a `ping`
round trip, and reads the tip off `getbestblockhash`. A new connection
follows each drop, and the block sent in a non-canonical encoding is
followed by one too, as in Core's run. Each new connection waits for the
node's `getheaders`, as Core's `bootstrap_p2p` does. The other steps go
over RPC: `invalidateblock`, `setmocktime`, `getrawmempool` and
`gettxout`.

The log half asserts besides each refused block's reason in the node's
own log, as Core's `send_blocks_and_test` does (`Capability.DEBUG_LOG`).

Each body asks for `Capability.TEST_ACTIVATION_HEIGHT`, for the BIP34
height the sequence's first block needs, `Capability.INVALIDATE_BLOCK`,
`Capability.CLOCK` and `Capability.PEER_TIMEOUT`.

What differs from Core's file, beyond what the sequence's module
docstring lists:

- the re-org at the end of the sequence is `_REORG_SIZE` blocks long,
  short of Core's `LARGE_REORG_SIZE`;
- Core's harness starts every node with a `peertimeout` (`write_config`,
  `test_framework/util.py`); here the node is restarted with it, so that
  `CConnman::InactivityCheck` (`src/net.cpp`) does not drop the peer
  once the node's clock is two hours ahead;
- Core starts its node with `-prevoutfetchthreads=8` besides, an option
  of Core's block validation that a bitcoind before `v32.0` refuses as an
  invalid parameter;
- the log half looks for a refusal for a failed script in the wording of
  the build under test, as `tests/integration/support/script_verify_flag`
  gives it.

Each `feature_block_<node>_test.py` module beside this one runs each
body, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from contextlib import nullcontext
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.block.genesis import genesis_block
from btclib.p2p import BlockPayload, GetData, Headers, Message

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.feature_block import (
    BIP34_HEIGHT,
    BatchStep,
    BlockStep,
    CoinStep,
    InvalidateStep,
    MempoolStep,
    MockTimeStep,
    TipStep,
    steps,
)
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Peer
from tests.integration.support.script_verify_flag import block_script_verify_flag_failed

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from btclib.block.block import Block

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.feature_block import Step
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["blocks_are_judged", "blocks_are_judged_and_logged"]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

_BIP34 = f"-testactivationheight=bip34@{BIP34_HEIGHT}"

# the wording `steps` gives a block failing script verification
_BLOCK_SCRIPT_FAILED = "block-script-verify-flag-failed"

# what Core's `write_config` (`test_framework/util.py`) gives every node
# and the adapters do not: a `peertimeout` under which the moved clock
# drops no peer as inactive
_PEER_TIMEOUT = "-peertimeout=999999999"

# the length of the re-org at the end of the sequence: long enough that
# bitcoind fetches the alternative chain through its block download, as
# at `LARGE_REORG_SIZE`, rather than on the headers' announcement (its
# log reads `Large reorg, won't direct fetch`)
_REORG_SIZE = 33


def _debug_log(node: NodeAdapter, skip_counts: SkipCounts) -> Path:
    """Return the log the log half reads, or skip where the node keeps none.

    :raises TypeError: `node` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return node.debug_log_path


def _expecting(
    log_path: Path | None, reject_reason: str | None, script_failed: str
) -> AbstractContextManager[None]:
    """Return `assert_debug_log` over `log_path`, or nothing to assert.

    :param script_failed: the node's own wording for a failed script.
    """
    if log_path is None or reject_reason is None:
        return nullcontext()
    if reject_reason.startswith(_BLOCK_SCRIPT_FAILED):
        reject_reason = script_failed + reject_reason.removeprefix(_BLOCK_SCRIPT_FAILED)
    return assert_debug_log(log_path, [reject_reason])


def _connect(node: NodeAdapter) -> Peer:
    """Core's own `bootstrap_p2p`: a handshake, `getheaders`, then a ping."""
    peer = Peer(node.p2p_address, _MAGIC)
    try:
        peer.handshake()
        peer.wait_for("getheaders")
        peer.sync_with_ping()
    except BaseException:
        peer.close()
        raise
    return peer


def _block_message(block: Block) -> bytes:
    """Return the `block` message carrying `block`."""
    payload = BlockPayload(block, include_witness=True, check_validity=False)
    return payload.to_message(_MAGIC, check_validity=False).serialize()


def _payload(step: BlockStep) -> bytes:
    """Return the `block` message carrying the step's block, or its stand-in."""
    if step.serialized is None:
        return _block_message(step.block)
    message = Message(_MAGIC, "block", step.serialized, check_validity=False)
    return message.serialize(check_validity=False)


class _DataStore:
    """Core's own `P2PDataStore`: one peer, answering `getdata` from its blocks.

    The node may ask for a block sent earlier on the same connection,
    one it had no reason to keep when it came unasked. A new connection
    starts with an empty store, as Core's `bootstrap_p2p` does.
    """

    def __init__(self, node: NodeAdapter) -> None:
        self.node = node
        self.script_failed = block_script_verify_flag_failed(node)
        self.peer = _connect(node)
        self.blocks: dict[bytes, bytes] = {}

    def reconnect(self) -> None:
        """Close the connection and open a new one."""
        self.peer.close()
        self.peer = _connect(self.node)
        self.blocks = {}

    def close(self) -> None:
        """Close the connection."""
        self.peer.close()

    def _answer_until_asked_for(self, block_hash: bytes) -> None:
        """Answer each `getdata` until one names `block_hash`, in one wait."""

        def answer(message: Message) -> bool:
            items = GetData.parse(message.payload, check_validity=False).items
            for item in items:
                if item.hash in self.blocks:
                    self.peer.send_raw(self.blocks[item.hash])
            return any(item.hash == block_hash for item in items)

        self.peer.wait_for("getdata", predicate=answer)

    def feed(self, step: BlockStep, log_path: Path | None) -> None:
        """Core's own `send_blocks([block], ...)`: one block and its verdict."""
        block_hash = step.block.header.hash
        self.blocks[block_hash] = _payload(step)
        with _expecting(log_path, step.reject_reason, self.script_failed):
            if step.unrequested:
                self.peer.send_raw(self.blocks[block_hash])
            else:
                self.peer.send(Headers([step.block.header], check_validity=False))
                self._answer_until_asked_for(block_hash)
            if step.disconnects:
                self.peer.wait_for_disconnect()
            else:
                self.peer.sync_with_ping()
            if step.becomes_tip:
                wait_until(
                    lambda: self.node.rpc.call("getbestblockhash") == block_hash.hex()
                )
            else:
                assert self.node.rpc.call("getbestblockhash") != block_hash.hex()
        if step.disconnects or step.serialized is not None:
            self.reconnect()

    def feed_batch(self, step: BatchStep) -> None:
        """Core's own `send_blocks(blocks)`: one announcement, one verdict."""
        for block in step.blocks:
            self.blocks[block.header.hash] = _block_message(block)
        last_hash = step.blocks[-1].header.hash
        headers = [block.header for block in step.blocks]
        self.peer.send(Headers(headers, check_validity=False))
        self._answer_until_asked_for(last_hash)
        self.peer.sync_with_ping()
        if step.becomes_tip:
            wait_until(
                lambda: self.node.rpc.call("getbestblockhash") == last_hash.hex()
            )
        else:
            assert self.node.rpc.call("getbestblockhash") != last_hash.hex()


def _take(store: _DataStore, step: Step, log_path: Path | None) -> None:
    """Take one step: a block over p2p, anything else over RPC."""
    node = store.node
    match step:
        case BlockStep():
            store.feed(step, log_path)
        case BatchStep():
            store.feed_batch(step)
        case TipStep():
            assert node.rpc.call("getbestblockhash") == step.block_hash.hex()
        case InvalidateStep():
            node.rpc.call("invalidateblock", [step.block_hash.hex()])
        case MockTimeStep():
            node.set_mock_time(step.timestamp)
        case MempoolStep():
            mempool = node.rpc.call("getrawmempool")
            assert set(mempool) == {tx_id.hex() for tx_id in step.tx_ids}
        case CoinStep():
            coin = node.rpc.call("gettxout", [step.tx_id.hex(), step.vout])
            assert coin["confirmations"] == step.confirmations


def _run(node: NodeAdapter, log_path: Path | None) -> None:
    """Feed the sequence to `node`, at genesis, BIP34 from `BIP34_HEIGHT`.

    :param log_path: the node's own log where the body is the log half.
    """
    genesis_hash = genesis_block("regtest").header.hash
    assert node.rpc.call("getbestblockhash") == genesis_hash.hex()
    store = _DataStore(node)
    try:
        for step in steps(int(time.time()), reorg_size=_REORG_SIZE):
            try:
                _take(store, step, log_path)
            except Exception as error:
                error.add_note(f"at {step!r:.200}")
                raise
    finally:
        store.close()


def _start(cluster: _Cluster, skip_counts: SkipCounts) -> NodeAdapter:
    """Return the node, once it declares every capability the run needs."""
    (node,) = cluster(1)
    require(Capability.TEST_ACTIVATION_HEIGHT, node.capabilities, skip_counts)
    require(Capability.INVALIDATE_BLOCK, node.capabilities, skip_counts)
    require(Capability.CLOCK, node.capabilities, skip_counts)
    require(Capability.PEER_TIMEOUT, node.capabilities, skip_counts)
    return node


def blocks_are_judged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the wire half: each block taken, refused or left off the tip.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start(cluster, skip_counts)
    node.restart([_BIP34, _PEER_TIMEOUT])
    _run(node, None)


def blocks_are_judged_and_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half: each refusal logged with Core's own reject reason.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start(cluster, skip_counts)
    log_path = _debug_log(node, skip_counts)
    node.restart([_BIP34, _PEER_TIMEOUT])
    _run(node, log_path)
