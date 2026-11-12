# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_blocksonly`, as bodies over each node.

Read from Core's `test/functional/p2p_blocksonly.py` (`22535b460568`,
2026-10-13): a peer a node takes no transactions from is dropped for
sending or announcing one. Each of Core's two checks is a wire half and a
log half (`Capability.DEBUG_LOG`), each body over a fresh node:

- a `-blocksonly` node (`Capability.BLOCKS_ONLY`) reports no
  `localrelay`, drops an inbound peer sending a `tx` or announcing one in
  an `inv`, and still relays a transaction submitted over RPC to a peer
  asking for it. Restarted with `-whitelist=relay@127.0.0.1`, it offers
  a peer holding the `relay` permission transaction relay in its
  `version`, and a transaction such a peer sends it is accepted and
  relayed to another. The log half asserts Core's own `transaction sent
  in violation of protocol` and `inv sent in violation of protocol` lines
  and the `received getdata` the relay causes.
- a node relaying transactions drops a block-relay-only peer
  (`Capability.TYPED_OUTBOUND`) sending a `tx` or announcing one in an
  `inv`, answers a block-relay-only peer's `getdata` for a transaction
  with nothing, not even a `notfound`, and announces a transaction
  submitted over RPC to an outbound full-relay peer and to no
  block-relay-only peer once its clock (`Capability.CLOCK`) has moved on
  a minute, as Core's does. The log half asserts the two violation
  lines.

Every body mines a coin to spend first (`Capability.MINE`,
`mini_wallet.MiniWallet`).

Core's `P2PInterface` asks for whatever a node announces, and its
`P2PTxInvStore` records the announced transactions, from its framework's
network thread. Here `RelayConn` (`p2p_conns_test.py`) does both on the
test's own thread, where a wait reads the peer's connection: a check
that a peer was not announced a transaction reads it up to the `pong`
answering the second of two `ping`s, the node running its own send loop
for the peer in between (`Peer.sync_with_ping`).

What differs from Core's file besides:

- Core runs its second check over its first check's node, restarted with
  `-noblocksonly` after mining the relayed transaction; here the second
  check starts a fresh node, whose default is to relay transactions, and
  the first mines nothing after its relay;
- every block-relay-only peer listens on a port the OS chooses
  (`peer.Listener`), where Core's reuses one `p2p_idx`.

Each `p2p_blocksonly_<node>_test.py` module beside this one runs each
body, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from contextlib import ExitStack, nullcontext
from typing import TYPE_CHECKING

from btclib.p2p import (
    GetData,
    Inv,
    Inventory,
    InventoryType,
    TxPayload,
)
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.mini_wallet import MiniWallet
from btclib_functional_tests.node import wait_until
from tests.integration.p2p_conns_test import RelayConn

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from btclib.tx.tx import Tx

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_block_relay_only_peer_is_refused_transactions",
    "a_block_relay_only_peer_refusal_is_logged",
    "a_blocksonly_node_refusal_is_logged",
    "a_blocksonly_node_refuses_transactions",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

# Core's own default wait, `P2PInterface`'s and `wait_until`'s
_WAIT = 60.0

# Core's own `CInv` hashes (`p2p_blocksonly.py`)
_INV_HASH = 0x1234
_GETDATA_HASH = 0x12345


def _hash(value: int) -> bytes:
    """Core's own `CInv(h=value)` hash, in the order a block explorer prints."""
    return value.to_bytes(32, "big")


def _peer_info(node: NodeAdapter) -> list[dict[str, object]]:
    """Return the node's own `getpeerinfo`."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return peers


def _mempool_size(node: NodeAdapter) -> object:
    """Return the node's own `getmempoolinfo` `size`."""
    return node.rpc.call("getmempoolinfo")["size"]


def _disconnect_p2ps(node: NodeAdapter, stack: ExitStack) -> None:
    """Core's own `disconnect_p2ps`: every test peer closed, and awaited gone.

    The node has no peer of any other kind, so its own `getpeerinfo`
    empty is every test peer gone.
    """
    stack.close()
    wait_until(lambda: not _peer_info(node), timeout=_WAIT)


def _debug_log(node: NodeAdapter, skip_counts: SkipCounts) -> Path:
    """Return the log a log half reads, or skip where the node keeps none.

    :raises TypeError: `node` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return node.debug_log_path


def _expecting(
    log_path: Path | None, expected: Sequence[str]
) -> AbstractContextManager[None]:
    """Return `assert_debug_log` over `log_path`, or nothing for a wire half."""
    if log_path is None:
        return nullcontext()
    return assert_debug_log(log_path, expected)


def _node(
    cluster: _Cluster, skip_counts: SkipCounts, *capabilities: Capability, log: bool
) -> tuple[NodeAdapter, MiniWallet, Path | None]:
    """Return a fresh node, a wallet holding a coin to spend, and its log.

    :param capabilities: what the check is about, asked for first.
    :param log: whether the body is a log half, asking for the node's log.
    """
    (node,) = cluster(1)
    for capability in capabilities:
        require(capability, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    log_path = _debug_log(node, skip_counts) if log else None
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 1)
    return node, wallet, log_path


def _tx_violation(
    node: NodeAdapter, conn: RelayConn, wallet: MiniWallet, log_path: Path | None
) -> Tx:
    """Core's own `check_p2p_tx_violation`, over the node's first peer.

    :returns: the transaction the peer sent, which the node did not take.
    """
    tx = wallet.create_self_transfer()
    with _expecting(
        log_path, ["transaction sent in violation of protocol, disconnecting peer=0"]
    ):
        conn.send(TxPayload(tx, include_witness=True))
        conn.wait_for_disconnect()
        assert _mempool_size(node) == 0
    return tx


def _inv_violation(conn: RelayConn, log_path: Path | None, expected: str) -> None:
    """Core's own `inv` of a wtxid the node drops the peer for sending."""
    with _expecting(log_path, [expected]):
        conn.send(Inv([Inventory(InventoryType.MSG_WTX, _hash(_INV_HASH))]))
        conn.wait_for_disconnect()


def _blocksonly_mode(
    node: NodeAdapter, wallet: MiniWallet, log_path: Path | None
) -> None:
    """Core's own `blocksonly_mode_tests`.

    :param log_path: the node's own log where the check is a log half.
    """
    node.restart(["-blocksonly"])
    assert node.rpc.call("getnetworkinfo")["localrelay"] is False

    with ExitStack() as stack:
        tx = _tx_violation(node, RelayConn.inbound(stack, node), wallet, log_path)
        _disconnect_p2ps(node, stack)
    tx_hex = tx.serialize(True).hex()

    # a transaction `inv` violates the protocol too
    with ExitStack() as stack:
        _inv_violation(
            RelayConn.inbound(stack, node),
            log_path,
            f"transaction ({_INV_HASH:064x}) inv sent in violation of protocol,"
            " disconnecting peer",
        )

        # a transaction submitted over RPC is taken and relayed
        tx_relay_peer = RelayConn.inbound(stack, node)
        assert _peer_info(node)[0]["relaytxes"] is True
        accept = node.rpc.call("testmempoolaccept", [[tx_hex]])
        assert accept[0]["allowed"] is True
        with _expecting(log_path, [f"received getdata for: wtx {tx.hash.hex()} peer"]):
            node.rpc.call("sendrawtransaction", [tx_hex])
            tx_relay_peer.wait_for_tx(tx.id)
            assert _mempool_size(node) == 1

    # the relay permission, over an empty mempool
    node.restart(["-persistmempool=0", "-whitelist=relay@127.0.0.1", "-blocksonly"])
    assert node.rpc.call("getrawmempool") == []
    with ExitStack() as stack:
        first_peer = RelayConn.inbound(stack, node)
        second_peer = RelayConn.inbound(stack, node)
        peer_1_info, peer_2_info = _peer_info(node)
        assert peer_1_info["permissions"] == ["relay"]
        assert first_peer.version.relay is True
        assert peer_2_info["permissions"] == ["relay"]
        accept = node.rpc.call("testmempoolaccept", [[tx_hex]])
        assert accept[0]["allowed"] is True

        # a peer holding it has its transaction taken, and relayed
        with _expecting(log_path, ["received getdata"]):
            first_peer.send(TxPayload(tx, include_witness=True))
            assert first_peer.peer.is_connected
            second_peer.wait_for_tx(tx.id)
            assert _mempool_size(node) == 1


def _block_relay_conn(
    node: NodeAdapter, wallet: MiniWallet, log_path: Path | None
) -> None:
    """Core's own `blocks_relay_conn_tests`.

    :param log_path: the node's own log where the check is a log half.
    """
    assert node.rpc.call("getnetworkinfo")["localrelay"] is True

    # a block-relay-only peer sending a transaction is dropped
    with ExitStack() as stack:
        conn = RelayConn.outbound(stack, node, connection_type="block-relay-only")
        assert _peer_info(node)[0]["relaytxes"] is False
        tx = _tx_violation(node, conn, wallet, log_path)
        _disconnect_p2ps(node, stack)

    # and one announcing a transaction
    with ExitStack() as stack:
        conn = RelayConn.outbound(stack, node, connection_type="block-relay-only")
        assert _peer_info(node)[0]["relaytxes"] is False
        _inv_violation(
            conn, log_path, "inv sent in violation of protocol, disconnecting peer"
        )
        _disconnect_p2ps(node, stack)

    # a block-relay-only peer's `getdata` for a transaction is ignored
    with ExitStack() as stack:
        conn = RelayConn.outbound(stack, node, connection_type="block-relay-only")
        conn.send_and_ping(
            GetData([Inventory(InventoryType.MSG_WTX, _hash(_GETDATA_HASH))])
        )
        assert _peer_info(node)[0]["relaytxes"] is False
        assert "notfound" not in conn.peer.last_message

        # a transaction submitted over RPC is announced to a full-relay
        # peer and to no block-relay-only one
        conn = RelayConn.outbound(stack, node, connection_type="block-relay-only")
        tx_relay_peer = RelayConn.outbound(stack, node)
        node.rpc.call("sendrawtransaction", [tx.serialize(True).hex()])
        # Core's own minute, for an announcement timer to have fired
        node.set_mock_time(int(time.time()) + 60)
        tx_relay_peer.sync_with_ping()
        conn.sync_with_ping()
        assert tx.hash in tx_relay_peer.tx_invs
        assert tx.hash not in conn.tx_invs


def a_blocksonly_node_refuses_transactions(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half: a `-blocksonly` node's peers send no transaction.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(cluster, skip_counts, Capability.BLOCKS_ONLY, log=False)
    _blocksonly_mode(node, wallet, None)


def a_blocksonly_node_refusal_is_logged(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the log half: each refusal and the relay are logged.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, log_path = _node(
        cluster, skip_counts, Capability.BLOCKS_ONLY, log=True
    )
    _blocksonly_mode(node, wallet, log_path)


def a_block_relay_only_peer_is_refused_transactions(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half: a block-relay-only peer exchanges no transaction.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(
        cluster,
        skip_counts,
        Capability.TYPED_OUTBOUND,
        Capability.CLOCK,
        log=False,
    )
    _block_relay_conn(node, wallet, None)


def a_block_relay_only_peer_refusal_is_logged(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the log half: each refusal of a block-relay-only peer is logged.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, log_path = _node(
        cluster,
        skip_counts,
        Capability.TYPED_OUTBOUND,
        Capability.CLOCK,
        log=True,
    )
    _block_relay_conn(node, wallet, log_path)
