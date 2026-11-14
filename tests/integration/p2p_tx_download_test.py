# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_tx_download`, as bodies over each node.

Read from Core's `test/functional/p2p_tx_download.py` (`1278a5970d5a`,
2026-08-06): which of the peers announcing a transaction a node asks for
it, and when. Each of Core's checks is a body over a fresh node:

- a request that expires, a peer that disconnects and a peer answering
  `notfound` each have the node ask the other peer announcing the same
  transaction;
- among peers ready to be asked, a preferred one is asked first once the
  request in flight expires;
- an outbound peer (`Capability.TYPED_OUTBOUND`) and an inbound one with
  the `noban` permission are asked at once, any other inbound peer after
  Core's `NONPREF_PEER_TX_DELAY`;
- a peer announcing by txid is asked after Core's `TXID_RELAY_DELAY`
  where a peer announcing by wtxid is connected too, and at once where
  none is;
- one `inv` naming more transactions than Core's
  `MAX_PEER_TX_ANNOUNCEMENTS` has only that many asked for, unless the
  peer holds the `relay` permission;
- the entries of one `inv` naming the same transaction twice are
  processed once, and the entries naming what the peer's own `wtxidrelay`
  does not announce are not (`Capability.DEBUG_LOG`);
- a `notfound` for nothing asked is ignored;
- a peer is asked for at most Core's `MAX_PEER_TX_REQUEST_IN_FLIGHT`
  transactions at a time until Core's `OVERLOADED_PEER_TX_DELAY` passes;
- a transaction announced by inbound peers that never send it reaches a
  second node from the node that node dials (`Capability.CONNECT`);
- each inbound peer announcing a transaction it never sends is asked for
  it in turn;
- a transaction the node rejected is not asked for again until a block
  comes in, over a mempool `mempool_util.fill_mempool` has filled under
  `-maxmempool` (`Capability.MAXMEMPOOL`);
- an `inv` whose kind the peer's own `wtxidrelay` does not announce is
  ignored.

A body moves the node's clock (`Capability.CLOCK`) where Core's does. A
node ignores every transaction announced to it in initial block download,
which Core's cached chain is out of and a fresh node is in, so every body
mines a block first (`Capability.MINE`), and a body spending a coin mines
it the same way (`mini_wallet.MiniWallet`).

Core's `TestP2PConn` counts each `getdata` for a transaction from its
framework's network thread. Here `_TxConn`, a `Conn` (`support/p2p_conns/`),
counts it on the test's own thread, where the test reads the peer's
connection: a wait for a request reads each peer it names up to the
`pong` of a ping round trip ahead of every poll, the node sending that
`pong` only once its own send loop for the peer has run. Where Core's
check that a peer was not asked reads the count at once after the node's
clock moves, this one reads it after a round trip. A peer announcing by
txid is `Peer.handshake(wtxidrelay=False)`.

What differs from Core's file besides:

- Core starts every node with `-maxmempool=5` and `-persistmempool=0`;
  the rejection check alone, which fills the mempool, restarts its node
  with the first, and no node here is restarted over a mempool it had
  saved;
- that check's restart passes a `-datacarriersize` too
  (`Capability.DATACARRIER`), over the `OP_RETURN` padding `fill_mempool`
  adds, which a bitcoind without bitcoin/bitcoin#32406, first in
  `v30.0rc1`, refuses by default with `scriptpubkey`;
- Core runs its last five checks over two linked nodes, ahead of the
  first three adding ten inbound peers to each; here the second node is
  the inv-block check's alone, the in-flight check has its one peer and
  the tx-requests check its ten, each over one node;
- the inv-block check reads `getpeerinfo`'s `inv_to_send` only where the
  build's own `help getpeerinfo` names it (bitcoin/bitcoin#33448, which
  `v31.0rc1` is the first tag to carry);
- the spurious `notfound` is followed by a ping round trip, where Core
  sends it and asserts nothing;
- the fallback checks announce by `MSG_TX` where the node sent no
  BIP339 `wtxidrelay`, as btcd sends none, and by `MSG_WTX` as Core's
  do where it did;
- Core's harness starts every node with a `peertimeout` (`write_config`,
  `test_framework/util.py`) and this port passes none: no body moves the
  node's clock as far as `TIMEOUT_INTERVAL` (`src/net.h`), which
  `CConnman::InactivityCheck` (`src/net.cpp`) drops a peer that has
  completed its handshake for passing without a message either way.

The entries of one `inv` naming the same transaction are processed once
in `v32.0` and later, from
bitcoin/bitcoin@1278a5970d5ada0979052a5bad899e896b8ab40b, which
`v32.0rc1` is the first tag to carry: a bitcoind whose `getnetworkinfo`
`version` reads older than that
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35))
processes each entry, which is asserted there instead. A `master` build
between that change's merge and the version's move to `32.99` reads
older and processes each once all the same, the constant's own comment
having the commits.

Each `p2p_tx_download_<node>_test.py` module beside this one runs each
body, `tests/integration/conftest.py`'s own module docstring having how.
The btcd one leaves out the `relay` body,
`a_large_inv_is_capped_without_the_relay_permission`, its own docstring
saying why.
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from typing import TYPE_CHECKING, override

from btclib.amount import sats_from_btc
from btclib.p2p import (
    GetData,
    Inv,
    Inventory,
    InventoryType,
    NotFound,
    TxPayload,
)
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mempool_util import fill_mempool
from btclib_functional_tests.mini_wallet import PADDING_DATACARRIER_SIZE, MiniWallet
from btclib_functional_tests.node import (
    connect_nodes,
    wait_until,
    wait_until_mempools_agree,
    wait_until_tips_agree,
)
from btclib_functional_tests.peer import Peer
from tests.integration.support.p2p_conns import Conn

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from btclib.p2p import Message, Version

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_disconnect_falls_back_to_another_peer",
    "a_large_inv_is_capped_without_the_relay_permission",
    "a_noban_peer_is_asked_at_once",
    "a_notfound_falls_back_to_another_peer",
    "a_ready_preferred_peer_is_asked_first",
    "a_rejected_tx_is_asked_for_again_after_a_block",
    "a_spurious_notfound_is_ignored",
    "a_tx_reaches_a_node_past_unresponsive_peers",
    "a_txid_peer_is_asked_at_once_without_a_wtxid_peer",
    "a_txid_peer_waits_beside_a_wtxid_peer",
    "an_expired_request_falls_back_to_another_peer",
    "an_inbound_peer_is_asked_after_the_delay",
    "an_inv_of_the_wrong_kind_is_ignored",
    "an_outbound_peer_is_asked_at_once",
    "duplicate_inv_entries_are_processed_once",
    "every_announcing_peer_is_asked_in_turn",
    "requests_in_flight_are_capped",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

# Core's own constants (`test_framework/p2p.py`)
_NONPREF_PEER_TX_DELAY = 2
_TXID_RELAY_DELAY = 2
_OVERLOADED_PEER_TX_DELAY = 2
_GETDATA_TX_INTERVAL = 60

# Core's own constants (`p2p_tx_download.py`)
_MAX_PEER_TX_REQUEST_IN_FLIGHT = 100
_MAX_PEER_TX_ANNOUNCEMENTS = 5000
_NUM_INBOUND = 10
_MAX_GETDATA_INBOUND_WAIT = (
    _GETDATA_TX_INTERVAL + _NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY
)

# Core's own `MSG_TYPE_MASK` (`test_framework/messages.py`)
_MSG_TYPE_MASK = 0xFFFFFFFF >> 2

# Core's own default wait, `P2PInterface`'s and `wait_until`'s
_WAIT = 60.0

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which the entries of one `inv`
# naming the same transaction are processed once: `v32.0`'s, `v32.0rc1`
# being the first tag carrying the change. A known limit: a `master` build
# from its merge (`5b008514db`, 2026-08-06) until the version moved to
# `32.99` (`f3fec67c3e`, 2026-09-11) reports `319900` and processes each
# once all the same, so this test fails against such a build
_DEDUPLICATES_INV_VERSION = 320000


def _hash(value: int) -> bytes:
    """Core's own `CInv(h=value)` hash, in the order a block explorer prints."""
    return value.to_bytes(32, "big")


def _inv(type_code: InventoryType, *values: int | bytes) -> Inv:
    """Core's own `msg_inv([CInv(t=type_code, h=value), ...])`."""
    return Inv(
        [
            Inventory(type_code, _hash(value) if isinstance(value, int) else value)
            for value in values
        ]
    )


class _TxConn(Conn):
    """Core's own `TestP2PConn`: a peer counting the transactions asked of it.

    `last_getdata` is the hashes of the latest `getdata`, Core's
    `last_message["getdata"]`. An `inv` is not asked for.
    """

    def __init__(self, peer: Peer, version: Version) -> None:
        super().__init__(peer, version)
        self.tx_getdata_count = 0
        self.last_getdata: list[bytes] = []

    @override
    def _handle(self, message: Message) -> None:
        """Core's `on_getdata`, and `P2PInterface`'s own `on_ping`."""
        super()._handle(message)
        if message.command == "getdata":
            items = GetData.parse(message.payload).items
            self.last_getdata = [item.hash for item in items]
            for item in items:
                if item.type_code & _MSG_TYPE_MASK in (
                    InventoryType.MSG_TX,
                    InventoryType.MSG_WTX,
                ):
                    self.tx_getdata_count += 1


def _serving_wait_until(
    conns: Sequence[_TxConn], predicate: Callable[[], bool], *, timeout: float = _WAIT
) -> None:
    """Core's own `P2PInterface.wait_until`, each peer read ahead of a poll."""

    def _served() -> bool:
        for conn in conns:
            conn.sync_with_ping()
        return predicate()

    wait_until(_served, timeout=timeout)


def _wait_for_getdata(conn: _TxConn, hashes: list[bytes]) -> None:
    """Core's own `wait_for_getdata`: the latest `getdata` names `hashes`."""
    _serving_wait_until([conn], lambda: conn.last_getdata == hashes)


def _peer_info(node: NodeAdapter) -> list[dict[str, object]]:
    """Return the node's own `getpeerinfo`."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return peers


def _node(
    cluster: _Cluster, skip_counts: SkipCounts, *capabilities: Capability
) -> tuple[NodeAdapter, MiniWallet]:
    """Return a fresh node out of initial block download, and its wallet.

    :param capabilities: what the body asks for besides `Capability.MINE`,
        asked for first.
    """
    (node,) = cluster(1)
    for capability in capabilities:
        require(capability, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(1)
    return node, wallet


def _debug_log(node: NodeAdapter, skip_counts: SkipCounts) -> Path:
    """Return the log a log check reads, or skip where the node keeps none.

    :raises TypeError: `node` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return node.debug_log_path


def _tx_type(conn: _TxConn) -> InventoryType:
    """Return the kind `conn` announces a transaction by.

    `MSG_WTX` where the node sent BIP339's `wtxidrelay`, which a node
    ignores from a peer that did not negotiate it; `MSG_TX` otherwise.
    """
    if conn.peer.message_count["wtxidrelay"]:
        return InventoryType.MSG_WTX
    return InventoryType.MSG_TX


def _fallback(
    cluster: _Cluster, skip_counts: SkipCounts, tx_hash: int, *capabilities: Capability
) -> tuple[NodeAdapter, ExitStack, _TxConn, _TxConn]:
    """Core's own opening of each fallback check: two peers, one asked.

    :returns: the node, what closes both peers, the peer asked and the
        peer not asked.
    """
    node, _ = _node(cluster, skip_counts, *capabilities)
    stack = ExitStack()
    try:
        peer1 = _TxConn.inbound(stack, node)
        peer2 = _TxConn.inbound(stack, node)
        for peer in (peer1, peer2):
            peer.send(_inv(_tx_type(peer), tx_hash))
        # one of the peers is asked for the transaction
        _serving_wait_until(
            [peer1, peer2],
            lambda: peer1.tx_getdata_count + peer2.tx_getdata_count == 1,
        )
        asked, fallback = (
            (peer1, peer2) if peer1.tx_getdata_count == 1 else (peer2, peer1)
        )
        assert fallback.tx_getdata_count == 0
    except BaseException:
        stack.close()
        raise
    return node, stack, asked, fallback


def an_expired_request_falls_back_to_another_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_expiry_fallback`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, stack, _, fallback = _fallback(cluster, skip_counts, 0xFFAA, Capability.CLOCK)
    with stack:
        # the request to the other peer expires
        node.set_mock_time(int(time.time()) + _GETDATA_TX_INTERVAL + 1)
        _serving_wait_until(
            [fallback], lambda: fallback.tx_getdata_count >= 1, timeout=1
        )


def a_disconnect_falls_back_to_another_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_disconnect_fallback`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _, stack, asked, fallback = _fallback(cluster, skip_counts, 0xFFBB)
    with stack:
        asked.peer.close()
        _serving_wait_until(
            [fallback], lambda: fallback.tx_getdata_count >= 1, timeout=1
        )


def a_notfound_falls_back_to_another_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_notfound_fallback`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _, stack, asked, fallback = _fallback(cluster, skip_counts, 0xFFDD)
    with stack:
        asked.send_and_ping(NotFound([Inventory(_tx_type(asked), _hash(0xFFDD))]))
        _serving_wait_until(
            [fallback], lambda: fallback.tx_getdata_count >= 1, timeout=1
        )


def _preferred_inv(
    cluster: _Cluster, skip_counts: SkipCounts, connection_type: str
) -> None:
    """Core's own `test_preferred_inv`.

    :param connection_type: `inbound`, `outbound` or `whitelist`, Core's
        own `ConnectionType`.
    """
    capabilities = [Capability.CLOCK]
    if connection_type == "outbound":
        capabilities.insert(0, Capability.TYPED_OUTBOUND)
    node, _ = _node(cluster, skip_counts, *capabilities)
    if connection_type == "whitelist":
        node.restart(["-whitelist=noban@127.0.0.1"])

    mock_time = int(time.time() + 1)
    node.set_mock_time(mock_time)
    with ExitStack() as stack:
        peer = (
            _TxConn.outbound(stack, node)
            if connection_type == "outbound"
            else _TxConn.inbound(stack, node)
        )
        peer.send_and_ping(_inv(InventoryType.MSG_WTX, 0xFF00FF00))
        if connection_type == "inbound":
            assert peer.tx_getdata_count == 0
            node.set_mock_time(mock_time + _NONPREF_PEER_TX_DELAY)
        _serving_wait_until([peer], lambda: peer.tx_getdata_count >= 1, timeout=1)


def an_inbound_peer_is_asked_after_the_delay(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_preferred_inv(ConnectionType.INBOUND)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _preferred_inv(cluster, skip_counts, "inbound")


def an_outbound_peer_is_asked_at_once(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_preferred_inv(ConnectionType.OUTBOUND)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _preferred_inv(cluster, skip_counts, "outbound")


def a_noban_peer_is_asked_at_once(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Core's own `test_preferred_inv(ConnectionType.WHITELIST)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _preferred_inv(cluster, skip_counts, "whitelist")


def a_ready_preferred_peer_is_asked_first(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_preferred_tiebreaker_inv`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _node(cluster, skip_counts, Capability.TYPED_OUTBOUND, Capability.CLOCK)
    mock_time = int(time.time())
    node.set_mock_time(mock_time)
    inv = _inv(InventoryType.MSG_WTX, 0xFF00FF00)
    with ExitStack() as stack:
        # a peer asked at once that never answers, leaving two requests
        # ready once it expires, one preferred and one not
        unresponsive_peer = _TxConn.outbound(stack, node)
        unresponsive_peer.send_and_ping(inv)
        _serving_wait_until(
            [unresponsive_peer],
            lambda: unresponsive_peer.tx_getdata_count >= 1,
            timeout=1,
        )

        # inbound, non-preferred peers announcing the same transaction
        non_pref_peers = []
        for _ in range(_NUM_INBOUND):
            non_pref_peers.append(_TxConn.inbound(stack, node))
            non_pref_peers[-1].send_and_ping(inv)

        # none is asked while the request is in flight
        mock_time += _NONPREF_PEER_TX_DELAY
        node.set_mock_time(mock_time)
        for peer in non_pref_peers:
            peer.sync_with_ping()
            assert peer.tx_getdata_count == 0

        # a preferred peer, ready once it announces
        pref_peer = _TxConn.outbound(stack, node)
        pref_peer.send_and_ping(inv)

        assert len(_peer_info(node)) == _NUM_INBOUND + 2

        # it waits for the request in flight too
        assert pref_peer.tx_getdata_count == 0

        # the request in flight expires
        mock_time += _GETDATA_TX_INTERVAL - _NONPREF_PEER_TX_DELAY
        node.set_mock_time(mock_time)

        # the preferred peer is asked next
        _serving_wait_until(
            [pref_peer], lambda: pref_peer.tx_getdata_count >= 1, timeout=10
        )

        # and none of the others
        for peer in non_pref_peers:
            peer.sync_with_ping()
            assert peer.tx_getdata_count == 0


def _txid_inv_delay(
    cluster: _Cluster, skip_counts: SkipCounts, *, glob_wtxid: bool
) -> None:
    """Core's own `test_txid_inv_delay`.

    :param glob_wtxid: whether a peer announcing by wtxid is connected
        too, Core's own parameter.
    """
    node, _ = _node(cluster, skip_counts, Capability.CLOCK)
    node.restart(["-whitelist=noban@127.0.0.1"])
    mock_time = int(time.time() + 1)
    node.set_mock_time(mock_time)
    with ExitStack() as stack:
        peer = _TxConn.inbound(stack, node, wtxidrelay=False)
        if glob_wtxid:
            # a wtxid peer, without which `TXID_RELAY_DELAY` is waived
            _TxConn.inbound(stack, node, wtxidrelay=True)
        peer.send_and_ping(_inv(InventoryType.MSG_TX, 0xFF11FF11))
        assert peer.tx_getdata_count == (0 if glob_wtxid else 1)
        node.set_mock_time(mock_time + _TXID_RELAY_DELAY)
        _serving_wait_until([peer], lambda: peer.tx_getdata_count >= 1, timeout=1)


def a_txid_peer_is_asked_at_once_without_a_wtxid_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_txid_inv_delay()`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _txid_inv_delay(cluster, skip_counts, glob_wtxid=False)


def a_txid_peer_waits_beside_a_wtxid_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_txid_inv_delay(True)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _txid_inv_delay(cluster, skip_counts, glob_wtxid=True)


def a_large_inv_is_capped_without_the_relay_permission(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_large_inv_batch`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _node(cluster, skip_counts)
    inv = _inv(InventoryType.MSG_WTX, *range(_MAX_PEER_TX_ANNOUNCEMENTS + 1))

    # with the relay permission
    node.restart(["-whitelist=relay@127.0.0.1"])
    with ExitStack() as stack:
        peer = _TxConn.inbound(stack, node)
        peer.send(inv)
        _serving_wait_until(
            [peer], lambda: peer.tx_getdata_count == _MAX_PEER_TX_ANNOUNCEMENTS + 1
        )

    # without it
    node.restart()
    with ExitStack() as stack:
        peer = _TxConn.inbound(stack, node)
        peer.send(inv)
        _serving_wait_until(
            [peer], lambda: peer.tx_getdata_count == _MAX_PEER_TX_ANNOUNCEMENTS
        )
        peer.sync_with_ping()


def _deduplicates_inv(node: NodeAdapter) -> bool:
    """Whether `node` processes the entries of one `inv` naming one hash once.

    Core's own claim for every node, bitcoind before
    `_DEDUPLICATES_INV_VERSION` excepted: that build processes each entry,
    read off its own `getnetworkinfo` `version`
    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
    """
    if not isinstance(node, BitcoindAdapter):
        return True
    version: int = node.rpc.call("getnetworkinfo")["version"]
    return version >= _DEDUPLICATES_INV_VERSION


def duplicate_inv_entries_are_processed_once(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_duplicate_tx_inv`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    log_path = _debug_log(node, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    MiniWallet(node).generate(1)
    # how many times an entry repeated in one `inv` is processed
    repeats = 1 if _deduplicates_inv(node) else 2

    def send_invs_and_read_log(conn: _TxConn, items: list[Inventory]) -> str:
        log_start = log_path.stat().st_size
        conn.send_and_ping(Inv(items))
        with log_path.open(encoding="utf-8", errors="replace") as debug_log:
            debug_log.seek(log_start)
            return debug_log.read()

    tx, witness_tx, wtx = (
        InventoryType.MSG_TX,
        InventoryType.MSG_WITNESS_TX,
        InventoryType.MSG_WTX,
    )
    with ExitStack() as stack:
        for wtxidrelay, inv_type, inv_name, mismatched_type, mismatched_name, a, b in [
            (False, tx, "tx", wtx, "wtx", 0xAABBCC, 0xDDEEFF),
            (True, wtx, "wtx", tx, "tx", 0x112233, 0x445566),
        ]:
            conn = _TxConn.inbound(stack, node, wtxidrelay=wtxidrelay)
            inv_a_log = f"got inv: {inv_name} {a:064x}"
            inv_b_log = f"got inv: {inv_name} {b:064x}"
            mismatched_inv_log = f"got inv: {mismatched_name} {a:064x}"

            log = send_invs_and_read_log(
                conn,
                [
                    Inventory(mismatched_type, _hash(a)),
                    Inventory(inv_type, _hash(a)),
                    Inventory(inv_type, _hash(b)),
                    Inventory(inv_type, _hash(a)),
                    Inventory(inv_type, _hash(b)),
                ],
            )
            assert log.count(inv_a_log) == repeats
            assert log.count(inv_b_log) == repeats
            assert log.count(mismatched_inv_log) == 0

            # the duplicate filter is scoped to a single `inv`
            log = send_invs_and_read_log(
                conn, [Inventory(inv_type, _hash(a)), Inventory(inv_type, _hash(a))]
            )
            assert log.count(inv_a_log) == repeats
            assert log.count(inv_b_log) == 0

        # `MSG_TX` and `MSG_WITNESS_TX` are deduplicated as txids
        conn = _TxConn.inbound(stack, node, wtxidrelay=False)
        for first_type, first_name, second_type, second_name, a in [
            (tx, "tx", witness_tx, "witness-tx", 0x667788),
            (witness_tx, "witness-tx", tx, "tx", 0x778899),
        ]:
            log = send_invs_and_read_log(
                conn,
                [Inventory(first_type, _hash(a)), Inventory(second_type, _hash(a))],
            )
            assert log.count(f"got inv: {first_name} {a:064x}") == 1
            assert log.count(f"got inv: {second_name} {a:064x}") == repeats - 1

        # txids and wtxids are deduplicated separately
        conn = _TxConn.inbound(stack, node, wtxidrelay=True)
        for first_type, second_type, a in [
            (witness_tx, wtx, 0x8899AA),
            (wtx, witness_tx, 0x99AABB),
        ]:
            log = send_invs_and_read_log(
                conn,
                [Inventory(first_type, _hash(a)), Inventory(second_type, _hash(a))],
            )
            assert log.count(f"got inv: witness-tx {a:064x}") == 1
            assert log.count(f"got inv: wtx {a:064x}") == 1


def a_spurious_notfound_is_ignored(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Core's own `test_spurious_notfound`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _node(cluster, skip_counts)
    with ExitStack() as stack:
        conn = _TxConn.inbound(stack, node)
        conn.send_and_ping(NotFound([Inventory(InventoryType.MSG_TX, _hash(1))]))


def requests_in_flight_are_capped(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Core's own `test_in_flight_max`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _node(cluster, skip_counts, Capability.CLOCK)
    txids = range(_MAX_PEER_TX_REQUEST_IN_FLIGHT + 2)
    with ExitStack() as stack:
        peer = _TxConn.inbound(stack, node)
        mock_time = int(time.time() + 1)
        node.set_mock_time(mock_time)
        for txid in txids[:_MAX_PEER_TX_REQUEST_IN_FLIGHT]:
            peer.send(_inv(InventoryType.MSG_WTX, txid))
        peer.sync_with_ping()
        mock_time += _NONPREF_PEER_TX_DELAY
        node.set_mock_time(mock_time)
        _serving_wait_until(
            [peer], lambda: peer.tx_getdata_count >= _MAX_PEER_TX_REQUEST_IN_FLIGHT
        )
        for txid in txids[_MAX_PEER_TX_REQUEST_IN_FLIGHT:]:
            peer.send(_inv(InventoryType.MSG_WTX, txid))
        peer.sync_with_ping()

        # no more requests within a second of the overloaded delay
        node.set_mock_time(
            mock_time + _NONPREF_PEER_TX_DELAY + _OVERLOADED_PEER_TX_DELAY - 1
        )
        peer.sync_with_ping()
        assert peer.tx_getdata_count == _MAX_PEER_TX_REQUEST_IN_FLIGHT

        # and the rest once it has passed
        node.set_mock_time(
            mock_time + _NONPREF_PEER_TX_DELAY + _OVERLOADED_PEER_TX_DELAY
        )
        _serving_wait_until([peer], lambda: peer.tx_getdata_count == len(txids))


def _reports_inv_to_send(node: NodeAdapter) -> bool:
    """Return whether `node`'s own `help getpeerinfo` names `inv_to_send`."""
    help_text = node.rpc.call("help", ["getpeerinfo"])
    assert isinstance(help_text, str)
    return "inv_to_send" in help_text


def a_tx_reaches_a_node_past_unresponsive_peers(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_inv_block`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node0, node1 = cluster(2)
    require(Capability.CLOCK, node0.capabilities, skip_counts)
    require(Capability.CONNECT, node1.capabilities, skip_counts)
    require(Capability.MINE, node0.capabilities, skip_counts)
    reports_inv_to_send = _reports_inv_to_send(node0)
    wallet = MiniWallet(node0)
    wallet.generate(COINBASE_MATURITY + 1)
    connect_nodes(node1, node0)
    wait_until_tips_agree([node0, node1])

    with ExitStack() as stack:
        peers = [
            _TxConn.inbound(stack, node)
            for node in (node0, node1)
            for _ in range(_NUM_INBOUND)
        ]

        tx = wallet.create_self_transfer()
        mock_time = int(time.time())
        node0.set_mock_time(mock_time)

        # every peer of each node announces the transaction and never sends it
        inv = _inv(InventoryType.MSG_WTX, tx.hash)
        for peer in peers:
            peer.send_and_ping(inv)

        node0.rpc.call("sendrawtransaction", [tx.serialize(True).hex()])

        # node1 is outbound to node0 and asks it for the transaction once
        # the request to one of its inbound peers expires; node0 sees the
        # connection as inbound and announces the transaction on its own
        # schedule, which the clock moved past that expiry covers
        assert _peer_info(node0)[0]["inbound"] is True
        if reports_inv_to_send:
            assert _peer_info(node0)[0]["inv_to_send"] == 1
        assert _peer_info(node1)[0]["inbound"] is False
        mock_time += 2 + _NONPREF_PEER_TX_DELAY + _GETDATA_TX_INTERVAL
        node0.set_mock_time(mock_time)
        # an unrelated peer, so that node0 runs its send loop at least once
        peers[0].sync_with_ping()
        if reports_inv_to_send:
            # Core's own comment: may fail rarely
            assert _peer_info(node0)[0]["inv_to_send"] == 0
        wait_until_mempools_agree([node0, node1], timeout=_WAIT)

        node0.set_mock_time(0)


def every_announcing_peer_is_asked_in_turn(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_tx_requests`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _ = _node(cluster, skip_counts, Capability.CLOCK)
    txid = _hash(0xDEADBEEF)
    with ExitStack() as stack:
        peers = [_TxConn.inbound(stack, node) for _ in range(_NUM_INBOUND)]
        for peer in peers:
            peer.send_and_ping(_inv(InventoryType.MSG_WTX, txid))

        def getdata_found(peer: _TxConn) -> bool:
            return bool(peer.last_getdata) and peer.last_getdata[-1] == txid

        outstanding = list(peers)
        mock_time = int(time.time())
        while outstanding:
            mock_time += _MAX_GETDATA_INBOUND_WAIT
            node.set_mock_time(mock_time)
            _serving_wait_until(
                outstanding,
                lambda: any(getdata_found(peer) for peer in outstanding),  # noqa: B023
            )
            outstanding = [peer for peer in outstanding if not getdata_found(peer)]

        node.set_mock_time(0)


def a_rejected_tx_is_asked_for_again_after_a_block(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_rejects_filter_reset`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(
        cluster,
        skip_counts,
        Capability.CLOCK,
        Capability.MAXMEMPOOL,
        Capability.DATACARRIER,
    )
    wallet.generate(COINBASE_MATURITY)
    node.restart(["-maxmempool=5", f"-datacarriersize={PADDING_DATACARRIER_SIZE}"])
    fill_mempool(node)
    wallet.resync()
    mempoolminfee = node.rpc.call("getmempoolinfo")["mempoolminfee"]
    with ExitStack() as stack:
        peer = _TxConn.inbound(stack, node)
        low_fee_tx = wallet.create_self_transfer(
            fee_rate=sats_from_btc(mempoolminfee) * 9 // 10
        )
        accept = node.rpc.call(
            "testmempoolaccept", [[low_fee_tx.serialize(True).hex()]]
        )
        assert accept[0]["reject-reason"] == "mempool min fee not met"
        peer.send_and_ping(TxPayload(low_fee_tx, include_witness=True))
        inv = _inv(InventoryType.MSG_WTX, low_fee_tx.hash)
        peer.send_and_ping(inv)
        mock_time = int(time.time())
        node.set_mock_time(mock_time)
        mock_time += _MAX_GETDATA_INBOUND_WAIT
        node.set_mock_time(mock_time)
        peer.sync_with_ping()
        assert peer.tx_getdata_count == 0

        # the rejection filter is cleared once a new block comes in
        wallet.generate(1)
        peer.sync_with_ping()
        peer.send_and_ping(inv)
        mock_time += _MAX_GETDATA_INBOUND_WAIT
        node.set_mock_time(mock_time)
        _wait_for_getdata(peer, [low_fee_tx.hash])


def an_inv_of_the_wrong_kind_is_ignored(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_inv_wtxidrelay_mismatch`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, Capability.CLOCK)
    wallet.generate(COINBASE_MATURITY)
    with ExitStack() as stack:
        wtxidrelay_on_peer = _TxConn.inbound(stack, node, wtxidrelay=True)
        wtxidrelay_off_peer = _TxConn.inbound(stack, node, wtxidrelay=False)
        random_tx = wallet.create_self_transfer()
        by_txid = _inv(InventoryType.MSG_TX, random_tx.id)
        by_wtxid = _inv(InventoryType.MSG_WTX, random_tx.hash)

        # `MSG_TX` from a wtxid peer, ignored
        wtxidrelay_on_peer.send_and_ping(by_txid)
        mock_time = int(time.time())
        node.set_mock_time(mock_time)
        mock_time += _MAX_GETDATA_INBOUND_WAIT
        node.set_mock_time(mock_time)
        wtxidrelay_on_peer.sync_with_ping()
        assert wtxidrelay_on_peer.tx_getdata_count == 0

        # `MSG_WTX` from a txid peer, ignored
        wtxidrelay_off_peer.send_and_ping(by_wtxid)
        mock_time += _MAX_GETDATA_INBOUND_WAIT
        node.set_mock_time(mock_time)
        wtxidrelay_off_peer.sync_with_ping()
        assert wtxidrelay_off_peer.tx_getdata_count == 0

        # `MSG_TX` from a txid peer, asked for
        wtxidrelay_off_peer.send_and_ping(by_txid)
        mock_time += _MAX_GETDATA_INBOUND_WAIT
        node.set_mock_time(mock_time)
        _wait_for_getdata(wtxidrelay_off_peer, [random_tx.id])

        # `MSG_WTX` from a wtxid peer, asked for
        wtxidrelay_on_peer.send_and_ping(by_wtxid)
        mock_time += _MAX_GETDATA_INBOUND_WAIT
        node.set_mock_time(mock_time)
        _wait_for_getdata(wtxidrelay_on_peer, [random_tx.hash])
