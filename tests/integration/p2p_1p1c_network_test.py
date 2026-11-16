# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_1p1c_network`, one body over each node.

Read from Core's `test/functional/p2p_1p1c_network.py` (`fa6f7e36a8c2`,
2026-10-23; `v32.1`'s copy differs only in a comment): packages
submitted to one node of a line reach every other node over p2p, a
parent paying too little alone relayed with the child paying for it
(1p1c), though some of those nodes were sent part of a package
beforehand -- keeping a child as an orphan, or refusing a parent for its
fee.

Core's `run_test` is the body, in its own order, over a line of nodes,
each dialling the one before it as Core's `setup_network` has them, and
every assertion of Core's own is kept:

- a peer of each node but the first sends it part of the packages: the
  second node every child and the first parent of the package of two
  parents, the third that parent alone, the last that parent and the
  parent of every other package. Every node's mempool then holds that
  parent alone, the one paying enough, and the second node keeps the
  children as orphans where no other node keeps any;
- once the peers disconnect, no node keeps an orphan;
- `submitpackage` takes each package on the first node, whose mempool
  then holds every transaction of every package, and every node's
  mempool comes to agree with it.

The packages are Core's own: a parent paying no fee and a child paying
for both, once with a witness and once with none; two parents above the
relay fee and a child of both; and a parent paying no fee into two
outputs, a child spending both.

The orphans are `Capability.ORPHANAGE`'s, asked for first, as the 1p1c
relay of `p2p_opportunistic_1p1c_test.py` asks for it, then
`Capability.PACKAGE_ACCEPTANCE`, for `submitpackage`, then
`Capability.CONNECT` and `Capability.MINE`. Every node restarts with the
`-whitelist=noban,in,out@127.0.0.1` Core's `noban_tx_relay` starts every
node with, which asks for no capability. The final agreement is a relay
across the line, each hop the time a node takes to announce and to ask:
Core's own waits bound it, scaled by `--timeout-factor`.

A bitcoind before `v31.0` refuses a package whose parent pays no fee:
bitcoin/bitcoin#33892, first in `v31.0rc1`, lets the child pay for it.
Core's own `v30.3` file instead starts each node with `-maxmempool=5`
(and `v29.4`'s with `-datacarriersize=100000` beside it, for
`fill_mempool`'s padding), fills the first node's mempool with
`fill_mempool` so that every node's minimum fee rate stands above the
relay fee, and has each parent pay that fee, the parent with two outputs
one satoshi per virtual byte, which the child spending both pays fifty
times over. The mempools then hold the filler too, so that file reads
neither their contents nor the orphanage before the peers leave, and only
waits for the mempools to agree once the packages are submitted. The body
reads the build's `getnetworkinfo` `version` against
`_ZERO_FEE_PARENT_VERSION`, bitcoin/bitcoin#33892 changing no RPC, and
does as that file does before it. A known limit: a `master` build from
that merge (`ec4ff99a22`) up to the move to `31.99` reports a `30.99`
version with the new rule, so the body fails against such a build.

What differs from Core's file:

- Core's second wallet is a `MiniWalletMode.RAW_P2PK` one on the third
  node: here `mine_p2pk_coins` (`support/p2pk_coins/`) mines, on that
  node, coinbases paying `RAW_P2PK_SCRIPT_PUB_KEY`, and
  `raw_p2pk_script_sig` (`mini_wallet.py`) signs every spend of one,
  every input signed under btclib's deterministic nonce where Core's
  draws signatures until the scriptSig is of one fixed length;
- Core's `rescan_utxos` reads its wallet's coins off the chain; the
  `MiniWallet` (`mini_wallet.py`) here caches the coins it mines;
- Core's `P2PInterface` asks for whatever the node announces; `Peer`
  (`peer.py`) asks for nothing, the checks reading no announcement;
- Core's `disconnect_p2ps` closes one node's peers and waits for it to
  count no test peer, node by node; here every peer closes, then each
  node waits to count the peers it had before its own connected;
- every fee Core writes in BTC is written here in satoshis, and every
  fee rate in satoshis per 1000 virtual bytes.

Each `p2p_1p1c_network_<node>_test.py` module beside this one runs the body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from contextlib import ExitStack
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import TxPayload
from btclib.tx import Tx

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mempool_util import fill_mempool
from btclib_functional_tests.mini_wallet import (
    PADDING_DATACARRIER_SIZE,
    MiniWallet,
    Utxo,
)
from btclib_functional_tests.node import (
    connect_nodes,
    sync_all,
    wait_until,
    wait_until_mempools_agree,
)
from btclib_functional_tests.peer import Peer
from tests.integration.support.p2pk_coins import (
    mine_p2pk_coins,
    p2pk_new_utxo,
    p2pk_self_transfer,
)
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from decimal import Decimal

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["every_node_takes_the_packages_one_node_is_given"]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

# Core's own `num_nodes`
_NUM_NODES = 4

# what Core's own `noban_tx_relay` starts every node with
_NOBAN = "-whitelist=noban,in,out@127.0.0.1"

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v31.0`, whose first tag,
# `v31.0rc1`, is the first to carry bitcoin/bitcoin#33892; that change
# alters no RPC
_ZERO_FEE_PARENT_VERSION = 310000

# what Core's own `v30.3` file starts every node with, and what
# `fill_mempool`'s padding needs on a build without bitcoin/bitcoin#32406
_MAXMEMPOOL = "-maxmempool=5"
_DATACARRIER = f"-datacarriersize={PADDING_DATACARRIER_SIZE}"

# the blocks Core's own `run_test` mines to each of its wallets
_P2PK_BLOCKS = 10
_WALLET_BLOCKS = 120

# Core's own `DEFAULT_MIN_RELAY_TX_FEE` (`test_framework/mempool_util.py`),
# in satoshis per 1000 virtual bytes
_DEFAULT_MIN_RELAY_TX_FEE = 100

# Core's own child fee rate: `999 * DEFAULT_MIN_RELAY_TX_FEE`
_HIGH_FEE_RATE = 999 * _DEFAULT_MIN_RELAY_TX_FEE

# Core's own `fee_per_output` of the child spending both outputs, in
# satoshis
_TWO_OUTPUTS_CHILD_FEE = 10_000

# Core's own orphans of the second node: the child of each package
_ORPHANS = 4

# the transactions of Core's own packages, what the first node's
# mempool holds once it has taken them
_PACKAGED_TXS = 9

# Core's own default wait, `wait_until`'s and `sync_mempools`', in seconds
_WAIT = 60.0


def _hex(tx: Tx) -> str:
    """Return `tx` serialized with its witness, the form every RPC takes."""
    return tx.serialize(True, check_validity=False).hex()


def _tx(tx: Tx) -> TxPayload:
    """Core's own `msg_tx(tx)`, its witness serialized."""
    return TxPayload(tx, include_witness=True, check_validity=False)


def _mempool(node: NodeAdapter) -> list[str]:
    """Return the node's own `getrawmempool`."""
    txids = node.rpc.call("getrawmempool")
    assert isinstance(txids, list)
    return txids


def _orphans(node: NodeAdapter) -> list[str]:
    """Return the node's own `getorphantxs`, the txid of each orphan."""
    txids = node.rpc.call("getorphantxs")
    assert isinstance(txids, list)
    return txids


def _peer_count(node: NodeAdapter) -> int:
    """Return how many peers the node's own `getpeerinfo` lists."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return len(peers)


def _takes_zero_fee_parents(node: NodeAdapter) -> bool:
    """Whether `node` takes a package whose parent pays no fee.

    Read off `bitcoind_version` against `_ZERO_FEE_PARENT_VERSION`. Any
    node but a bitcoind answers yes.
    """
    version = bitcoind_version(node)
    return version is None or version >= _ZERO_FEE_PARENT_VERSION


def _basic_1p1c(wallet: MiniWallet, parent_fee_rate: int) -> list[Tx]:
    """Core's own `create_basic_1p1c`, of its default wallet.

    A parent of a confirmed coin at `parent_fee_rate`, and a child of it
    at `_HIGH_FEE_RATE`.
    """
    parent = wallet.create_self_transfer(fee_rate=parent_fee_rate, confirmed_only=True)
    child = wallet.create_self_transfer(
        utxo_to_spend=wallet.new_utxos(parent)[0], fee_rate=_HIGH_FEE_RATE
    )
    return [parent, child]


def _p2pk_basic_1p1c(coin: Utxo, parent_fee_rate: int) -> list[Tx]:
    """Core's own `create_basic_1p1c`, of its `RAW_P2PK` wallet."""
    parent = p2pk_self_transfer(coin, parent_fee_rate)
    return [parent, p2pk_self_transfer(p2pk_new_utxo(parent), _HIGH_FEE_RATE)]


def _package_2p1c(wallet: MiniWallet) -> list[Tx]:
    """Core's own `create_package_2p1c`.

    Two parents of confirmed coins, at ten and twenty times
    `_DEFAULT_MIN_RELAY_TX_FEE`, and a child of both, its fee in satoshis
    999 times the first parent's own virtual size.
    """
    parent1 = wallet.create_self_transfer(
        fee_rate=_DEFAULT_MIN_RELAY_TX_FEE * 10, confirmed_only=True
    )
    parent2 = wallet.create_self_transfer(
        fee_rate=_DEFAULT_MIN_RELAY_TX_FEE * 20, confirmed_only=True
    )
    child = wallet.create_self_transfer_multi(
        utxos_to_spend=[wallet.new_utxos(parent1)[0], wallet.new_utxos(parent2)[0]],
        fee_per_output=999 * parent1.vsize,
    )
    return [parent1, parent2, child]


def _package_2outs(wallet: MiniWallet, *, zero_fee: bool) -> list[Tx]:
    """Core's own `create_package_2outs`.

    A parent of a confirmed coin, into two outputs, and a child spending
    both, in the reverse order. Where `zero_fee` the parent pays no fee and
    the child `_TWO_OUTPUTS_CHILD_FEE` an output; otherwise the parent
    pays one satoshi per virtual byte, rounded up, an output paying half
    of it, and the child's one output a hundred times that.
    """
    utxo = wallet.get_utxo(confirmed_only=True)
    if zero_fee:
        parent_fee = 0
        child_fee = _TWO_OUTPUTS_CHILD_FEE
    else:
        tester = wallet.create_self_transfer_multi(utxos_to_spend=[utxo], num_outputs=2)
        parent_fee = -(-tester.vsize // 2)
        child_fee = parent_fee * 100
    parent = wallet.create_self_transfer_multi(
        utxos_to_spend=[utxo], num_outputs=2, fee_per_output=parent_fee
    )
    child = wallet.create_self_transfer_multi(
        utxos_to_spend=wallet.new_utxos(parent)[::-1], fee_per_output=child_fee
    )
    return [parent, child]


def _raise_network_minfee(nodes: Sequence[NodeAdapter]) -> None:
    """Core's own `raise_network_minfee`, of a build before `v31.0`.

    The first node's mempool is filled until it evicts, and every node's
    minimum fee rate stands above the relay fee once the others hold
    the same transactions.
    """
    fill_mempool(nodes[0])
    for node in nodes:

        def _above(node: NodeAdapter = node) -> bool:
            info = node.rpc.call("getmempoolinfo")
            minfee: Decimal = info["mempoolminfee"]
            relayfee: Decimal = info["minrelaytxfee"]
            return minfee > relayfee

        wait_until(_above, timeout=_WAIT)


def _presend(
    nodes: Sequence[NodeAdapter],
    txs_to_presend: list[list[Tx]],
    *,
    only_parent_in_mempools: bool,
) -> None:
    """Have a peer of each node send it its `txs_to_presend` entry, then leave.

    Core's own `add_p2p_connection` for each node, its `send_and_ping`
    of each transaction, and its `disconnect_p2ps`: each peer closes, and
    each node comes to count the peers it had before its own connected.
    Where `only_parent_in_mempools`, the mempools and the orphanages are
    read before the peers leave.
    """
    peers_before = [_peer_count(node) for node in nodes]
    with ExitStack() as stack:
        peers = []
        for node in nodes:
            peer = stack.enter_context(Peer(node.p2p_address, _MAGIC))
            peer.handshake()
            peer.sync_with_ping()
            peers.append(peer)
        for peer, txs in zip(peers, txs_to_presend, strict=True):
            for tx in txs:
                peer.send(_tx(tx))
                peer.sync_with_ping()

        if only_parent_in_mempools:
            # the fee-having parent is the only thing in the mempools
            wait_until_mempools_agree(nodes, timeout=_WAIT)
            sufficient_parent = txs_to_presend[2][0].id.hex()
            for i, node in enumerate(nodes):
                # the second node has a non-empty orphanage as well
                if i == 1:
                    assert len(_orphans(node)) == _ORPHANS, _orphans(node)
                else:
                    assert _orphans(node) == [], _orphans(node)
                assert _mempool(node) == [sufficient_parent], _mempool(node)

    for node, count in zip(nodes, peers_before, strict=True):

        def _back_to_before(node: NodeAdapter = node, count: int = count) -> bool:
            return _peer_count(node) == count

        wait_until(_back_to_before, timeout=_WAIT)


def every_node_takes_the_packages_one_node_is_given(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes = cluster(_NUM_NODES)
    zero_fee = _takes_zero_fee_parents(nodes[0])
    capabilities = [
        Capability.ORPHANAGE,
        Capability.PACKAGE_ACCEPTANCE,
        Capability.CONNECT,
        Capability.MINE,
    ]
    if not zero_fee:
        capabilities.append(Capability.MAXMEMPOOL)
    for capability in capabilities:
        require(capability, nodes[0].capabilities, skip_counts)
    for node in nodes:
        node.restart([_NOBAN] if zero_fee else [_NOBAN, _MAXMEMPOOL, _DATACARRIER])
    for first, second in zip(nodes[1:], nodes, strict=False):
        connect_nodes(first, second)

    p2pk = mine_p2pk_coins(nodes[2], _P2PK_BLOCKS)
    sync_all(nodes)
    wallet = MiniWallet(nodes[1])
    wallet.generate(_WALLET_BLOCKS)
    sync_all(nodes)

    if not zero_fee:
        _raise_network_minfee(nodes)

    parent_fee_rate = 0 if zero_fee else _DEFAULT_MIN_RELAY_TX_FEE
    package_1 = _basic_1p1c(wallet, parent_fee_rate)
    package_2 = _p2pk_basic_1p1c(p2pk[0], parent_fee_rate)
    package_3 = _package_2p1c(wallet)
    package_4 = _package_2outs(wallet, zero_fee=zero_fee)
    parent_31 = package_3[0]
    # node0: sender; node1: the children, kept as orphans until its peer
    # disconnects; node3: the parents, refused for their fee; every node
    # but the first is sent `parent_31` ahead of time
    _presend(
        nodes,
        [
            [],
            [package_1[1], package_2[1], parent_31, package_3[2], package_4[1]],
            [parent_31],
            [package_1[0], package_2[0], parent_31, package_4[0]],
        ],
        only_parent_in_mempools=zero_fee,
    )

    # the peers' disconnection clears their outstanding orphan requests
    for node in nodes:

        def _no_orphan(node: NodeAdapter = node) -> bool:
            return len(_orphans(node)) == 0

        wait_until(_no_orphan, timeout=_WAIT)

    # submit full packages to the first node
    for package in (package_1, package_2, package_3, package_4):
        result = nodes[0].rpc.call("submitpackage", [[_hex(tx) for tx in package]])
        assert isinstance(result, dict), result
        assert result["package_msg"] == "success", result

    # wait for mempools to sync
    if zero_fee:
        wait_until(lambda: len(_mempool(nodes[0])) == _PACKAGED_TXS, timeout=_WAIT)
    wait_until_mempools_agree(nodes, timeout=_WAIT)
