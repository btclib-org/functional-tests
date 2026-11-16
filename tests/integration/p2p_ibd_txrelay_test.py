# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_ibd_txrelay`, as bodies over each node.

Read from Core's `test/functional/p2p_ibd_txrelay.py` (`7777598f87ea`,
2026-10-22): transaction relay while a node is in initial block
download. Two linked nodes started with `-minrelaytxfee`
(`Capability.MIN_RELAY_TX_FEE`) each send the other a `feefilter` of
`MAX_MONEY` rounded down to `FeeFilterRounder`'s top bucket while in
initial block download, read off each node's own `getpeerinfo` as Core
reads it. A node kept there by a block older than the maximum tip age
asks a peer announcing a transaction for nothing, even once its clock
has moved past Core's `NONPREF_PEER_TX_DELAY`, and processes no
transaction a peer sends it unasked. Once a block within that age takes
both nodes out of it, each sends the other a `feefilter` of the minimum
relay fee, the node asks a peer announcing the old block's coinbase by
its wtxid for it, and processes the transaction it ignored before.

A bitcoind before `v31.0` fills its recently-confirmed filter with a block
connected during initial block download (bitcoin/bitcoin#34054), so it
asks for no such coinbase. No probe tells the two behaviours apart, the
node having no option or RPC for it: that build is asserted to ask for
nothing, read off its own `getnetworkinfo` `version`
([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).

Core's run is one sequence, and each body is that whole sequence over
two fresh nodes. The log half asserts besides what Core's
`assert_debug_log` asserts around each transaction sent unasked
(`Capability.DEBUG_LOG`): `received: tx` and no `was not accepted` in
initial block download, and `was not accepted` out of it. The wire half
sends each transaction all the same, and asserts of it only the ping
round trip after it. Each body asks for `Capability.MINE` and
`Capability.CLOCK` of the node under test, and `Capability.CONNECT` of
the node dialling it, as Core's `setup_network` has the second node
dial the first.

What differs from Core's file:

- Core builds the old block with `create_block` and has `generate` mine
  the block ending initial block download on the node's clock; here
  `build_next_block` (`mini_wallet.py`) builds each at the time Core's
  has, its coinbase paying `RAW_P2PK_SCRIPT_PUB_KEY`, and each is
  submitted over `submitblock`;
- Core's `P2PDataStore` records every `getdata` from its framework's
  network thread, and here `_DataStore` records those it reads in
  `Conn.sync_with_ping`;
- Core's `disconnect_p2ps` waits for the node to count no test peer, and
  here the wait is for the node's own `getpeerinfo` to name the other
  node alone;
- Core's `syncwithvalidationinterfacequeue` after the old block is
  bitcoind's own RPC (`src/rpc/blockchain.cpp`), so `_sync_validation_queue`
  calls it on bitcoind alone: the node fills its filter of recently
  confirmed transactions on events queued behind the block
  (`src/validationinterface.cpp`), and a late one would put the old
  block's coinbase there after the node has left initial block download.

Each `p2p_ibd_txrelay_<node>_test.py` module beside this one runs each body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from contextlib import nullcontext
from decimal import Decimal
from typing import TYPE_CHECKING, override

from bitcoin_core_rpc import magic_from_chain
from btclib.amount import btc_from_sats, sats_from_btc
from btclib.p2p import GetData, Inv, Inventory, InventoryType, TxPayload
from btclib.tx import Tx

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.mini_wallet import (
    RAW_P2PK_SCRIPT_PUB_KEY,
    build_next_block,
)
from btclib_functional_tests.node import connect_nodes, sync_all, wait_until
from btclib_functional_tests.peer import Peer
from tests.integration.support.p2p_conns import Conn
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from btclib.p2p import Message, Version

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["ibd_tx_relay_is_logged", "ibd_tx_relay_is_withheld"]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

# Core's own `MAX_FEE_FILTER` and `NORMAL_FEE_FILTER` (`p2p_ibd_txrelay.py`),
# in satoshis per 1000 virtual bytes
_MAX_FEE_FILTER = 9936506
_NORMAL_FEE_FILTER = 10

# what Core's own `extra_args` start each node with
_MIN_RELAY_TX_FEE = f"-minrelaytxfee={btc_from_sats(_NORMAL_FEE_FILTER):.8f}"

# Core's own `NONPREF_PEER_TX_DELAY` (`test_framework/p2p.py`)
_NONPREF_PEER_TX_DELAY = 2

# how far behind the node's clock Core stamps the block keeping it in
# initial block download
_OLD_BLOCK_AGE = 2 * 24 * 60 * 60

# the wtxid Core's own peer announces during initial block download
_TXID = 0xDEADBEEF

# Core's own transaction from `tx_valid.json`: well formed, and spending
# an output no chain here holds
_RAW_TX_HEX = (
    "0100000001b14bdcbc3e01bdaad36cc08e81e69c82e1060bc14e518db2b49aa43ad90ba2"
    "60000000004a01ff47304402203f16c6f40162ab686621ef3000b04e75418a0c0cb2d8ae"
    "beac894ae360ac1e780220ddc15ecdfc3507ac48e1681a33eb60996631bf6bf5bc0a0682"
    "c4db743ce7ca2b01ffffffff0140420f00000000001976a914660d4ef3a743e3e696ad99"
    "0364e555c271ad504b88ac00000000"
)

# Core's own `CLIENT_VERSION`, at or past which a block connected during
# initial block download leaves the recently-confirmed filter alone: `v31.0`'s
# (bitcoin/bitcoin#34054). A known limit: a `master` build from that change's
# merge (`af99643454`, 2026-02-26) until the version moved to `31.99`
# (`48b952cbb6`, 2026-03-06) reports `309900` and behaves as `v31.0` all the
# same, so the coinbase step fails against such a build
_IBD_BLOCKS_NOT_FILTERED_VERSION = 310000

# Core's own default wait, `wait_until`'s and `wait_for_getdata`'s
_WAIT = 60.0


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
    log_path: Path | None,
    expected: Sequence[str],
    unexpected: Sequence[str] = (),
) -> AbstractContextManager[None]:
    """Return `assert_debug_log` over `log_path`, or nothing to assert."""
    if log_path is None:
        return nullcontext()
    return assert_debug_log(log_path, expected, unexpected)


def _in_ibd(node: NodeAdapter) -> bool:
    """Return `getblockchaininfo`'s own `initialblockdownload`."""
    info = node.rpc.call("getblockchaininfo")
    assert isinstance(info, dict)
    in_ibd = info["initialblockdownload"]
    assert isinstance(in_ibd, bool)
    return in_ibd


def _peer_info(node: NodeAdapter) -> list[dict[str, object]]:
    """Return the node's own `getpeerinfo`."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return peers


def _fee_filters(node: NodeAdapter) -> list[int]:
    """Return each peer's own `minfeefilter`, in sat/kvB, off `getpeerinfo`."""
    fee_filters = []
    for peer in _peer_info(node):
        fee_filter = peer["minfeefilter"]
        assert isinstance(fee_filter, Decimal)
        fee_filters.append(sats_from_btc(fee_filter))
    return fee_filters


def _wait_for_fee_filters(node: NodeAdapter, fee_filter: int) -> None:
    """Core's own wait for every peer's `minfeefilter` to read `fee_filter`.

    :param fee_filter: in satoshis per 1000 virtual bytes.
    """
    wait_until(
        lambda: all(each == fee_filter for each in _fee_filters(node)),
        timeout=_WAIT,
    )


def _submit_block(node: NodeAdapter, block_time: int) -> bytes:
    """Build a block extending `node`'s tip at `block_time`, and submit it.

    :returns: the block's own hash.
    """
    block = build_next_block(node, RAW_P2PK_SCRIPT_PUB_KEY, time=block_time)
    answer = node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])
    assert answer is None
    return block.header.hash


class _DataStore(Conn):
    """Core's own `P2PDataStore`, as far as the `getdata` it records.

    `requests` is every hash a `getdata` read named.
    """

    def __init__(self, peer: Peer, version: Version) -> None:
        super().__init__(peer, version)
        self.requests: set[bytes] = set()

    @override
    def _handle(self, message: Message) -> None:
        """Core's `P2PDataStore.on_getdata`, and `Conn`'s `on_ping`."""
        super()._handle(message)
        if message.command == "getdata":
            self.requests.update(
                item.hash for item in GetData.parse(message.payload).items
            )


def _connect(node: NodeAdapter) -> _DataStore:
    """Core's own `add_p2p_connection`: a handshake, then a ping round trip.

    The round trip is `Peer.sync_with_ping`'s, which records no `getdata`.
    """
    peer = Peer(node.p2p_address, _MAGIC)
    try:
        store = _DataStore(peer, peer.handshake())
        peer.sync_with_ping()
    except BaseException:
        peer.close()
        raise
    return store


def _disconnect_p2ps(node: NodeAdapter) -> None:
    """Core's own `disconnect_p2ps`, `node`'s test peer already closed.

    The node's other peer is the second node alone.
    """
    wait_until(lambda: len(_peer_info(node)) == 1, timeout=_WAIT)


def _asks_for(wtxid: bytes) -> Callable[[Message], bool]:
    """Return whether a `getdata` names `wtxid` alone: `wait_for_getdata`."""

    def predicate(message: Message) -> bool:
        return [item.hash for item in GetData.parse(message.payload).items] == [wtxid]

    return predicate


def _sync_validation_queue(node: NodeAdapter) -> None:
    """Core's own `syncwithvalidationinterfacequeue`, on bitcoind alone."""
    if isinstance(node, BitcoindAdapter):
        node.rpc.call("syncwithvalidationinterfacequeue")


def _filters_ibd_blocks(node: NodeAdapter) -> bool:
    """Whether `node` marks a block connected during IBD as recently confirmed.

    Only a bitcoind before `_IBD_BLOCKS_NOT_FILTERED_VERSION` does, read off
    its own `getnetworkinfo` `version`
    ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).
    """
    version = bitcoind_version(node)
    return version is not None and version < _IBD_BLOCKS_NOT_FILTERED_VERSION


def _wtx_inv(wtxid: bytes) -> Inv:
    """Core's own `msg_inv([CInv(t=MSG_WTX, h=wtxid)])`."""
    return Inv([Inventory(InventoryType.MSG_WTX, wtxid)])


def _send_tx(peer: Peer, tx: Tx) -> None:
    """Core's own `send_and_ping(msg_tx(tx))`."""
    peer.send(TxPayload(tx, include_witness=True, check_validity=False))
    peer.sync_with_ping()


def _run(node: NodeAdapter, other: NodeAdapter, log_path: Path | None) -> None:
    """Core's own `run_test`, over `node` and `other` already linked.

    :param node: Core's `self.nodes[0]`, the node under test.
    :param other: Core's `self.nodes[1]`.
    :param log_path: `node`'s own log where the body is the log half.
    """
    # both nodes send `MAX_MONEY`, rounded, while in initial block download
    for each in (node, other):
        assert _in_ibd(each)
        _wait_for_fee_filters(each, _MAX_FEE_FILTER)

    # a block older than the maximum tip age: still in initial block
    # download, and its coinbase's wtxid kept
    mock_time = int(time.time())
    node.set_mock_time(mock_time)
    old_block_hash = _submit_block(node, mock_time - _OLD_BLOCK_AGE)
    assert _in_ibd(node)
    _sync_validation_queue(node)
    old_block = node.rpc.call("getblock", [old_block_hash.hex(), 2])
    ibd_wtxid = bytes.fromhex(old_block["tx"][0]["hash"])

    # an announced transaction asked for by no `getdata`
    txid = _TXID.to_bytes(32, "big")
    store = _connect(node)
    with store.peer as peer:
        peer.send(_wtx_inv(txid))
        store.sync_with_ping()
        # were the node to ask, it would ask after this delay first
        mock_time += _NONPREF_PEER_TX_DELAY
        node.set_mock_time(mock_time)
        store.sync_with_ping()
        assert txid not in store.requests
    _disconnect_p2ps(node)

    # a transaction sent unasked, and left unprocessed
    assert isinstance(other.rpc.call("decoderawtransaction", [_RAW_TX_HEX]), dict)
    tx = Tx.parse(bytes.fromhex(_RAW_TX_HEX), check_validity=False)
    with (
        _connect(node).peer as peer,
        _expecting(log_path, ["received: tx"], ["was not accepted"]),
    ):
        _send_tx(peer, tx)
    _disconnect_p2ps(node)

    # out of initial block download on a block at the node's own clock
    _submit_block(node, mock_time)
    sync_all([node, other])
    for each in (node, other):
        assert not _in_ibd(each)
        _wait_for_fee_filters(each, _NORMAL_FEE_FILTER)

    # the coinbase confirmed during initial block download is asked for,
    # unless the build filtered it as recently confirmed
    store = _connect(node)
    with store.peer as peer:
        peer.send(_wtx_inv(ibd_wtxid))
        peer.sync_with_ping()
        mock_time += _NONPREF_PEER_TX_DELAY
        node.set_mock_time(mock_time)
        if _filters_ibd_blocks(node):
            store.sync_with_ping()
            assert ibd_wtxid not in store.requests
        else:
            peer.wait_for("getdata", predicate=_asks_for(ibd_wtxid), timeout=_WAIT)
    _disconnect_p2ps(node)

    # the same transaction, sent unasked, now processed
    with _connect(node).peer as peer, _expecting(log_path, ["was not accepted"]):
        _send_tx(peer, tx)


def _nodes(
    cluster: _Cluster, skip_counts: SkipCounts
) -> tuple[NodeAdapter, NodeAdapter]:
    """Return two fresh nodes, each started with Core's own `extra_args`.

    :returns: the node under test, and the node dialling it.
    """
    node, other = cluster(2)
    require(Capability.MINE, node.capabilities, skip_counts)
    require(Capability.CLOCK, node.capabilities, skip_counts)
    require(Capability.CONNECT, other.capabilities, skip_counts)
    require(Capability.MIN_RELAY_TX_FEE, node.capabilities, skip_counts)
    require(Capability.MIN_RELAY_TX_FEE, other.capabilities, skip_counts)
    return node, other


def _start(node: NodeAdapter, other: NodeAdapter) -> None:
    """Restart both nodes with `-minrelaytxfee`, then link them as Core does."""
    node.restart([_MIN_RELAY_TX_FEE])
    other.restart([_MIN_RELAY_TX_FEE])
    connect_nodes(other, node)


def ibd_tx_relay_is_withheld(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the wire half: filters, requests and relay in and out of IBD.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, other = _nodes(cluster, skip_counts)
    _start(node, other)
    _run(node, other, None)


def ibd_tx_relay_is_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half: each transaction sent unasked, in Core's own words.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, other = _nodes(cluster, skip_counts)
    log_path = _debug_log(node, skip_counts)
    _start(node, other)
    _run(node, other, log_path)
