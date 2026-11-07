# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_opportunistic_1p1c`, as bodies over each node.

Read from Core's `test/functional/p2p_opportunistic_1p1c.py`
(`86ff13a0af06`, 2026-10-01): a parent refused for its fee is taken
into the mempool with a child paying for both, the two evaluated
together as one parent and one child (1p1c) where one peer sent both.
Each of the checks below is one of Core's `test_*` methods, a body over
a fresh node:

- a parent refused for its fee, announced ahead of its child by an
  outbound peer (`Capability.TYPED_OUTBOUND`), is asked for again once
  the child arrives and taken in with it, and not asked of another
  outbound peer announcing it; once for a parent with a witness and
  once for one with none;
- a child arriving ahead of its parent is kept as an orphan and taken
  in with it;
- a child paying too little for both is refused with its parent, and
  neither is asked of another peer or taken in when the parent is sent
  again, while a later child paying enough is taken in with it; once for
  a parent with a witness and once for one with none;
- a child whose witness is invalid is not evaluated with its parent
  sent by another peer, and neither peer is dropped once the child's
  own peer sends that parent;
- a parent whose witness is invalid, sent by another peer, is refused
  alone and leaves the child kept, which is taken in once its own peer
  sends the valid parent;
- no parent of a child of two parents, each refused for its fee, is
  asked for;
- a child of two parents, one of them in the mempool, is taken in with
  the other, where the build evaluates such a package, and refused with
  it otherwise;
- a parent and child spending the child of another such pair are taken
  in on top of it;
- a child kept as an orphan, its parent asked for, is taken in with that
  parent while other peers fill the orphanage with large orphans, some
  evicted where the build bounds each peer's share of the orphanage, none
  where it bounds their number alone;
- other peers send small orphans while a child is kept as an orphan, its
  parent asked for: the same orphans from each of a set of peers, the
  first sending them and the rest announcing them, and others of its own
  from one more peer. Where the build bounds each peer's share of the
  orphanage, the set of peers fills it to its bound on announcements, and
  the child stays kept and is taken in with its parent; where it bounds
  their number alone, the orphanage holds that bound once they are sent,
  and the child is taken in with its parent if still kept, and not if
  evicted at random.

Every body asks for `Capability.ORPHANAGE` first. It starts its node
with `-maxmempool=5` (`Capability.MAXMEMPOOL`) and a `-datacarriersize`
(`Capability.DATACARRIER`) and fills the mempool with
`mempool_util.fill_mempool`, so that its minimum fee rate stands above
the relay one, and mines its coins first (`Capability.MINE`,
`mini_wallet.MiniWallet`). Every body but the one chaining a package on
another moves the node's clock (`Capability.CLOCK`) where Core's does,
from the wall clock at its start; that one, as Core's own, waits out the
node's delays on the wall clock.

Core builds a transaction with no witness, whose txid is its wtxid,
with a `MiniWalletMode.RAW_P2PK` wallet: here `mine_p2pk_coins`
(`p2pk_coins_test.py`) mines coinbases paying `RAW_P2PK_SCRIPT_PUB_KEY`
ahead of the wallet's own, and `raw_p2pk_script_sig` (`mini_wallet.py`)
signs every spend of one.

Core's `P2PInterface` records the latest `getdata` from its framework's
network thread, and asks for whatever the node announces. Here `RelayConn`
(`p2p_conns_test.py`) does both on the test's own thread, where a wait
reads the peer's connection up to the `pong` of a ping round trip ahead
of every poll.

What differs from Core's file besides:

- `v29.4` refuses the `OP_RETURN` padding `fill_mempool` adds, as an
  output larger than its default `-datacarriersize`, with `scriptpubkey`,
  and `v30.3` and later do not (bitcoin/bitcoin#32406, first in
  `v30.0rc1`), so every body's node starts with the option;
- Core runs every check over one node, filled once, and ends each with
  its `cleanup`, which asserts the mempool's minimum fee rate still
  above the relay one; here each body starts a fresh node, fills its
  mempool and asserts none of that;
- Core gives each transaction its `create_tx_below_mempoolminfee` makes
  a sequence number of its own, so that no two of its checks make one
  txid on its one node; here every input's sequence number is zero;
- Core's invalid child is a `createrawtransaction` paying the node's own
  deterministic address, given a witness of garbage; here it is a
  self-transfer paying the same fee, its witness replaced by
  `malleated_to_invalid_witness`;
- Core's `RAW_P2PK` wallet signs a transaction's first input alone,
  drawing signatures until the scriptSig is of one fixed length; here
  every input is signed, under btclib's deterministic nonce, whatever
  the length;
- the large-orphans check asks `testmempoolaccept` about each large
  orphan over a connection of its own per call where the node serves
  HTTP through libevent (`rpc_echo_payload_test.py`'s module
  docstring has why); Core's keeps one connection.

Core's node starts with `-inboundrelaypercent=100` besides, an option
bitcoin/bitcoin@0bd3d3dfa562724e3dbc4cec2d4290e4561655cd adds and
`v32.0rc1` is the first tag to carry, and which a release before it
refuses as unknown. Here the check sending many orphans, the one
connecting the most inbound peers, passes it to a bitcoind whose
`getnetworkinfo` `version` reads at or past `v32.0`'s, and to no other
node. The option sets the share of inbound slots that peers relaying
transactions may take (`CConnman::Init`, `src/net.h`), which a release
before it does not bound, and the share it defaults to admits every peer
that check connects: so the check asserts the same on either build, with
the option or without it. A `master` build between the option's merge
and the version's move to `32.99` reads older and starts without it, the
constant's own comment having the commits.

A build before `v30.0rc1` bounds the orphanage by count alone, at
`DEFAULT_MAX_ORPHAN_TRANSACTIONS` (`-maxorphantx`, `src/net_processing.h`),
evicting an orphan at random once the bound is passed
(`TxOrphanage::LimitOrphans`, `src/txorphanage.cpp`);
bitcoin/bitcoin#31829, of which `v30.0rc1` is the first tag, bounds each
peer's share instead. Core's own `v29.4` file has no check of either. The
two orphanage checks read the build off `getorphantxs`'s `help`, which
lists `expiration` before bitcoin/bitcoin#31829 and not after.

A bitcoind before bitcoin/bitcoin#31385, first in `v30.0rc1`, refuses a
child of two parents when one of them is in the mempool. Core's `v29.4`
file asserts that refusal, and so does the check on such a build.
bitcoin/bitcoin#31385 changes no RPC, so the check reads the build's
`getnetworkinfo` `version` against `_TWO_PARENT_VERSION`.

Each `p2p_opportunistic_1p1c_<node>_test.py` module beside this one runs
each body, `tests/integration/conftest.py`'s own module docstring having
how.
"""

from __future__ import annotations

import secrets
from contextlib import ExitStack
from typing import TYPE_CHECKING

from btclib.amount import sats_from_btc
from btclib.p2p import Inv, Inventory, InventoryType, TxPayload
from btclib.script.script import serialize
from btclib.script.script_pub_key import ScriptPubKey
from btclib.script.witness import Witness
from btclib.tx import OutPoint, Tx, TxIn, TxOut

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mempool_util import fill_mempool
from btclib_functional_tests.mini_wallet import (
    PADDING_DATACARRIER_SIZE,
    MiniWallet,
    Utxo,
)
from btclib_functional_tests.node import wait_until
from tests.integration.p2p_conns_test import Clock, RelayConn
from tests.integration.p2p_private_broadcast_test import malleated_to_invalid_witness
from tests.integration.p2pk_coins_test import (
    mine_p2pk_coins,
    p2pk_new_utxo,
    p2pk_self_transfer,
    p2pk_tx,
)
from tests.integration.rpc_echo_payload_test import (
    client_per_call,
    serves_through_libevent,
)
from tests.integration.script_verify_flag_test import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_package_is_taken_in_on_top_of_another",
    "a_rejected_parent_is_taken_in_only_with_a_child_paying_enough",
    "a_rejected_parent_is_taken_in_with_its_child",
    "a_rejected_parent_with_no_witness_is_taken_in_only_with_a_child_paying_enough",
    "a_rejected_parent_with_no_witness_is_taken_in_with_its_child",
    "an_invalid_parent_from_another_peer_leaves_the_orphan",
    "an_orphan_is_taken_in_with_its_low_fee_parent",
    "an_orphan_is_taken_in_with_one_parent_beside_another_in_the_mempool",
    "an_orphan_outlives_large_orphans_from_other_peers",
    "an_orphan_outlives_many_orphans_from_other_peers",
    "no_rejected_parent_of_a_two_parent_orphan_is_requested",
    "parent_and_child_are_evaluated_together_only_from_one_peer",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

# Core's own constants (`test_framework/p2p.py`)
_NONPREF_PEER_TX_DELAY = 2
_TXID_RELAY_DELAY = 2

# Core's own `GETDATA_WAIT` (`p2p_opportunistic_1p1c.py`)
_GETDATA_WAIT = 60

# Core's own default wait, `P2PInterface`'s and `wait_until`'s
_WAIT = 60.0

# Core's own `FEERATE_1SAT_VB` (`p2p_opportunistic_1p1c.py`), in satoshis
# per 1000 virtual bytes
_FEERATE_1SAT_VB = 1000

# Core's own `DEFAULT_MIN_RELAY_TX_FEE` (`test_framework/mempool_util.py`),
# in satoshis per 1000 virtual bytes
_DEFAULT_MIN_RELAY_TX_FEE = 100

# how far short of its coin Core's own invalid child pays, in satoshis:
# its `coin["value"] - Decimal("0.0001")`
_BAD_ORPHAN_FEE = 10_000

# what `fill_mempool` needs the node started with, as Core's own
# `extra_args` gives it
_MAXMEMPOOL = "-maxmempool=5"

# what `fill_mempool`'s padding needs on a build without bitcoin/bitcoin#32406
_DATACARRIER = f"-datacarriersize={PADDING_DATACARRIER_SIZE}"

# the rest of Core's own `extra_args`, which `_takes_inbound_relay_percent`
# says whether the running build takes
_INBOUND_RELAY_PERCENT = "-inboundrelaypercent=100"

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which bitcoind takes
# `-inboundrelaypercent`: `v32.0`'s, `v32.0rc1` being the first tag
# carrying it and a release before it refusing it. A known limit: a
# `master` build from its merge (`11ebbd9072`, 2026-07-24) until the
# version moved to `32.99` (`f3fec67c3e`, 2026-09-11) reports `319900` and
# takes it all the same, so it starts without it
_INBOUND_RELAY_PERCENT_VERSION = 320000

# Core's own `MAX_STANDARD_TX_WEIGHT` (`test_framework/blocktools.py`)
_MAX_STANDARD_TX_WEIGHT = 400_000

# the size of the one witness item Core's own `create_large_orphan`
# (`test_framework/mempool_util.py`) spends its input with
_LARGE_ORPHAN_WITNESS_SIZE = 390_000

# Core's own `test_orphanage_dos_large`: the large orphans it makes, the
# ones among them each sent by a peer of its own, and the virtual size of
# its orphan child, which bounds each large orphan's too
_LARGE_ORPHANS = 50
_INDIVIDUAL_DOSERS = 10
_LARGE_VSIZE = 100_000

# Core's own `test_orphanage_dos_many`: the small orphans each of a set of
# peers sends alike, how many peers send them, and the small orphans one
# more peer sends of its own
_BATCH_SIZE = 51
_NUM_PEERS_SHARED = 60
_BATCH_SINGLE_DOSER = 100

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v30.0`, whose first tag,
# `v30.0rc1`, is the first to carry bitcoin/bitcoin#31385, which relaxes the
# rule refusing a child with a parent in the mempool beside another.
# bitcoin/bitcoin#31385 changes no RPC, and `submitpackage`'s `help`
# describes the rule only from bitcoin/bitcoin#33630, first in `v30.1`. A
# known limit: a `master` build from that merge (`24246c3deb`) up to the
# move to `30.99` (`9f744fffc3`) reports `299900` with the relaxed rule, so
# this check fails against such a build
_TWO_PARENT_VERSION = 300000

# Core's own `DEFAULT_MAX_ORPHAN_TRANSACTIONS` (`src/net_processing.h`),
# the orphanage's bound on its size before bitcoin/bitcoin#31829
_MAX_ORPHAN_TRANSACTIONS = 100

# Core's own `DEFAULT_MAX_ORPHANAGE_LATENCY_SCORE` (`src/node/txorphanage.h`),
# the orphanage's bound on its latency score, to which each announcement
# of an orphan of fewer than ten inputs adds one (`GetLatencyScore`,
# `src/node/txorphanage.cpp`): Core's own `test_orphanage_dos_many`
# asserts that its announcements fill the orphanage to it
_DEFAULT_MAX_ORPHANAGE_LATENCY_SCORE = 3000


def _inv(tx: Tx) -> Inv:
    """Core's own `msg_inv([CInv(t=MSG_WTX, h=tx.wtxid_int)])`."""
    return Inv([Inventory(InventoryType.MSG_WTX, tx.hash)])


def _tx(tx: Tx) -> TxPayload:
    """Core's own `msg_tx(tx)`, its witness serialized."""
    return TxPayload(tx, include_witness=True, check_validity=False)


def _new_utxo(wallet: MiniWallet, tx: Tx) -> Utxo:
    """Core's own `new_utxo`: the first coin `tx` pays the wallet."""
    return wallet.new_utxos(tx)[0]


def _random_orphan(witness_item: bytes, data: bytes) -> Tx:
    """Return an orphan of one input and one output, its parent random.

    Its input spends an outpoint of a random txid, at a random index past
    the first, under a witness of `witness_item` alone; its output pays a
    hundred satoshis to an `OP_RETURN` of `data`.
    """
    prev_out = OutPoint(
        secrets.token_bytes(32), secrets.randbelow(99) + 1, check_validity=False
    )
    script = serialize(["OP_RETURN", data])
    return Tx(
        version=2,
        lock_time=0,
        vin=[TxIn(prev_out, script_witness=Witness([witness_item]))],
        vout=[TxOut(100, ScriptPubKey(script, check_validity=False))],
        check_validity=False,
    )


def _large_orphan() -> Tx:
    """Core's own `create_large_orphan` (`test_framework/mempool_util.py`).

    Its witness item is `_LARGE_ORPHAN_WITNESS_SIZE` bytes, its
    `OP_RETURN` twenty.
    """
    return _random_orphan(b"X" * _LARGE_ORPHAN_WITNESS_SIZE, b"a" * 20)


def _small_orphan() -> Tx:
    """Core's own `create_small_orphan` (`p2p_opportunistic_1p1c.py`).

    Its witness item is a script of five `OP_NOP`, its `OP_RETURN` three
    bytes.
    """
    return _random_orphan(serialize(["OP_NOP"] * 5), b"a" * 3)


def _orphans(node: NodeAdapter) -> list[str]:
    """Return the node's own `getorphantxs`, the txid of each orphan."""
    txids = node.rpc.call("getorphantxs")
    assert isinstance(txids, list)
    return txids


def _announcements(node: NodeAdapter) -> int:
    """Return how many announcements the node's orphanage holds.

    Each orphan's `from` in `getorphantxs` at verbosity 2, summed.
    """
    orphanage = node.rpc.call("getorphantxs", {"verbosity": 2})
    assert isinstance(orphanage, list)
    return sum(len(orphan["from"]) for orphan in orphanage)


def _mempool(node: NodeAdapter) -> list[str]:
    """Return the node's own `getrawmempool`."""
    txids = node.rpc.call("getrawmempool")
    assert isinstance(txids, list)
    return txids


def _mempoolminfee(node: NodeAdapter) -> int:
    """Return `getmempoolinfo`'s `mempoolminfee`, in satoshis per 1000 vB."""
    return sats_from_btc(node.rpc.call("getmempoolinfo")["mempoolminfee"])


def _takes_inbound_relay_percent(node: NodeAdapter) -> bool:
    """Whether `node` is a bitcoind taking `-inboundrelaypercent`.

    Read off its own `getnetworkinfo` `version`, against
    `_INBOUND_RELAY_PERCENT_VERSION`.
    """
    if not isinstance(node, BitcoindAdapter):
        return False
    version: int = node.rpc.call("getnetworkinfo")["version"]
    return version >= _INBOUND_RELAY_PERCENT_VERSION


def _help_says(node: NodeAdapter, rpc: str, text: str) -> bool:
    """Whether `rpc`'s own `help` on `node` has `text`."""
    answer = node.rpc.call("help", [rpc])
    assert isinstance(answer, str)
    return text in answer


def _takes_two_parent_package(node: NodeAdapter) -> bool:
    """Whether `node` evaluates a child with a parent already in its mempool.

    Read off `bitcoind_version` against `_TWO_PARENT_VERSION`. Any node but
    a bitcoind answers yes.
    """
    version = bitcoind_version(node)
    return version is None or version >= _TWO_PARENT_VERSION


def _bounds_orphans_by_count(node: NodeAdapter) -> bool:
    """Whether `node` bounds its orphanage by count alone, evicting at random.

    `getorphantxs`'s `help` lists `expiration` until bitcoin/bitcoin#31829.
    Any node but a bitcoind answers no.
    """
    if not isinstance(node, BitcoindAdapter):
        return False
    return _help_says(node, "getorphantxs", '"expiration"')


def _node(
    cluster: _Cluster,
    skip_counts: SkipCounts,
    *capabilities: Capability,
    coins: int,
    p2pk_coins: int = 0,
    inbound_relay_percent: bool = False,
) -> tuple[NodeAdapter, MiniWallet, list[Utxo]]:
    """Return a fresh node whose mempool is full, a wallet, and P2PK coins.

    The node restarts with `-maxmempool=5` and a `-datacarriersize`, then
    its coins are mined: `p2pk_coins` of them paying
    `RAW_P2PK_SCRIPT_PUB_KEY`, and `coins` to the wallet, all of which
    mature under the blocks `fill_mempool` mines before it fills the
    mempool.

    :param capabilities: what the body asks for besides
        `Capability.MAXMEMPOOL`, `Capability.DATACARRIER` and
        `Capability.MINE`, asked for after `Capability.ORPHANAGE`.
    :param inbound_relay_percent: restart with `-inboundrelaypercent=100`
        too, where `_takes_inbound_relay_percent` says the node takes it.
    """
    (node,) = cluster(1)
    for capability in (
        Capability.ORPHANAGE,
        *capabilities,
        Capability.MAXMEMPOOL,
        Capability.DATACARRIER,
        Capability.MINE,
    ):
        require(capability, node.capabilities, skip_counts)
    if inbound_relay_percent and _takes_inbound_relay_percent(node):
        node.restart([_MAXMEMPOOL, _DATACARRIER, _INBOUND_RELAY_PERCENT])
    else:
        node.restart([_MAXMEMPOOL, _DATACARRIER])
    p2pk = mine_p2pk_coins(node, p2pk_coins)
    wallet = MiniWallet(node)
    wallet.generate(coins)
    fill_mempool(node)
    wallet.resync()
    return node, wallet, p2pk


def _below_mempoolminfee(
    node: NodeAdapter, wallet: MiniWallet, utxo_to_spend: Utxo | None = None
) -> Tx:
    """Core's own `create_tx_below_mempoolminfee`, of its default wallet.

    A self-transfer at `_DEFAULT_MIN_RELAY_TX_FEE`, of a confirmed coin
    where `utxo_to_spend` is `None`, once the mempool's minimum fee rate
    is asserted above it.
    """
    assert _mempoolminfee(node) > _DEFAULT_MIN_RELAY_TX_FEE
    return wallet.create_self_transfer(
        fee_rate=_DEFAULT_MIN_RELAY_TX_FEE,
        utxo_to_spend=utxo_to_spend,
        confirmed_only=True,
    )


def _p2pk_below_mempoolminfee(node: NodeAdapter, coin: Utxo) -> Tx:
    """Core's own `create_tx_below_mempoolminfee`, of its `RAW_P2PK` wallet."""
    assert _mempoolminfee(node) > _DEFAULT_MIN_RELAY_TX_FEE
    return p2pk_self_transfer(coin, _DEFAULT_MIN_RELAY_TX_FEE)


def _low_fee_parent(node: NodeAdapter, wallet: MiniWallet, p2pk: list[Utxo]) -> Tx:
    """Return `_p2pk_below_mempoolminfee`'s where `p2pk` holds a coin.

    `_below_mempoolminfee`'s otherwise.
    """
    if p2pk:
        return _p2pk_below_mempoolminfee(node, p2pk[0])
    return _below_mempoolminfee(node, wallet)


def _child(wallet: MiniWallet, parent: Tx, fee_rate: int, *, witness: bool) -> Tx:
    """Core's own `create_self_transfer` of `parent`'s new coin at `fee_rate`.

    Of the wallet `parent` is of: the default one where `witness`, the
    `RAW_P2PK` one otherwise.
    """
    if witness:
        return wallet.create_self_transfer(
            utxo_to_spend=_new_utxo(wallet, parent), fee_rate=fee_rate
        )
    return p2pk_self_transfer(p2pk_new_utxo(parent), fee_rate)


def _parent_then_child(
    cluster: _Cluster, skip_counts: SkipCounts, *, witness: bool
) -> None:
    """Core's own `test_basic_parent_then_child`, of either wallet."""
    node, wallet, p2pk = _node(
        cluster,
        skip_counts,
        Capability.TYPED_OUTBOUND,
        Capability.CLOCK,
        coins=1,
        p2pk_coins=0 if witness else 1,
    )
    clock = Clock(node)
    low_fee_parent = _low_fee_parent(node, wallet, p2pk)
    high_fee_child = _child(
        wallet, low_fee_parent, 20 * _FEERATE_1SAT_VB, witness=witness
    )
    assert (low_fee_parent.id != low_fee_parent.hash) is witness
    with ExitStack() as stack:
        peer_sender = RelayConn.outbound(stack, node)
        peer_ignored = RelayConn.outbound(stack, node)

        # the parent is relayed first, and refused for its fee
        peer_sender.send_and_ping(_inv(low_fee_parent))
        peer_sender.wait_for_getdata([low_fee_parent.hash])
        peer_sender.send_and_ping(_tx(low_fee_parent))
        assert low_fee_parent.id.hex() not in _mempool(node)

        # another peer announcing it is not asked for it
        peer_ignored.send_and_ping(_inv(low_fee_parent))
        assert not peer_ignored.was_asked()

        # the child is relayed next, its input missing
        peer_sender.send_and_ping(_inv(high_fee_child))
        peer_sender.wait_for_getdata([high_fee_child.hash])
        peer_sender.send_and_ping(_tx(high_fee_child))

        # the parent is asked for by txid, refused as it was
        clock.bump(_TXID_RELAY_DELAY)
        peer_sender.wait_for_getdata([low_fee_parent.id])

        # and taken in with its child
        peer_sender.send_and_ping(_tx(low_fee_parent))
        node_mempool = _mempool(node)
        assert low_fee_parent.id.hex() in node_mempool
        assert high_fee_child.id.hex() in node_mempool


def a_rejected_parent_is_taken_in_with_its_child(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_basic_parent_then_child(self.wallet)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _parent_then_child(cluster, skip_counts, witness=True)


def a_rejected_parent_with_no_witness_is_taken_in_with_its_child(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_basic_parent_then_child(self.wallet_nonsegwit)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _parent_then_child(cluster, skip_counts, witness=False)


def an_orphan_is_taken_in_with_its_low_fee_parent(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_basic_child_then_parent`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(cluster, skip_counts, Capability.CLOCK, coins=1)
    clock = Clock(node)
    low_fee_parent = _below_mempoolminfee(node, wallet)
    high_fee_child = _child(wallet, low_fee_parent, 20 * _FEERATE_1SAT_VB, witness=True)
    with ExitStack() as stack:
        peer_sender = RelayConn.inbound(stack, node)

        # the child arrives first, its input missing
        peer_sender.send_and_ping(_inv(high_fee_child))
        clock.bump(_NONPREF_PEER_TX_DELAY)
        peer_sender.wait_for_getdata([high_fee_child.hash])
        peer_sender.send_and_ping(_tx(high_fee_child))

        # the parent is asked for by txid
        clock.bump(_NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY)
        peer_sender.wait_for_getdata([low_fee_parent.id])

        # and taken in with its child
        peer_sender.send_and_ping(_tx(low_fee_parent))
        node_mempool = _mempool(node)
        assert low_fee_parent.id.hex() in node_mempool
        assert high_fee_child.id.hex() in node_mempool


def _low_and_high_child(
    cluster: _Cluster, skip_counts: SkipCounts, *, witness: bool
) -> None:
    """Core's own `test_low_and_high_child`, of either wallet."""
    node, wallet, p2pk = _node(
        cluster,
        skip_counts,
        Capability.TYPED_OUTBOUND,
        Capability.CLOCK,
        coins=1,
        p2pk_coins=0 if witness else 1,
    )
    clock = Clock(node)
    low_fee_parent = _low_fee_parent(node, wallet, p2pk)
    # above the mempool's minimum fee rate, too little to pay for the parent
    med_fee_child = _child(
        wallet, low_fee_parent, _mempoolminfee(node), witness=witness
    )
    high_fee_child = _child(
        wallet, low_fee_parent, 999 * _FEERATE_1SAT_VB, witness=witness
    )
    with ExitStack() as stack:
        peer_sender = RelayConn.outbound(stack, node)
        peer_ignored = RelayConn.outbound(stack, node)

        # the parent is refused for its fee
        peer_sender.send_and_ping(_inv(low_fee_parent))
        peer_sender.wait_for_getdata([low_fee_parent.hash])
        peer_sender.send_and_ping(_tx(low_fee_parent))
        assert low_fee_parent.id.hex() not in _mempool(node)

        # another peer announcing it is not asked for it
        peer_ignored.send_and_ping(_inv(low_fee_parent))
        assert not peer_ignored.was_asked()

        # the child paying too little arrives, its input missing
        peer_sender.send_and_ping(_inv(med_fee_child))
        peer_sender.wait_for_getdata([med_fee_child.hash])
        peer_sender.send_and_ping(_tx(med_fee_child))

        # the parent is asked for by txid
        clock.bump(_TXID_RELAY_DELAY)
        peer_sender.wait_for_getdata([low_fee_parent.id])

        # the two are refused together, for their fee
        peer_sender.send_and_ping(_tx(low_fee_parent))
        node_mempool = _mempool(node)
        assert low_fee_parent.id.hex() not in node_mempool
        assert med_fee_child.id.hex() not in node_mempool

        # another peer announcing that child is not asked for it
        peer_ignored.send_and_ping(_inv(med_fee_child))
        assert not peer_ignored.was_asked()

        # the parent sent again, by either peer, is not taken in
        peer_sender.send_and_ping(_tx(low_fee_parent))
        peer_ignored.send_and_ping(_tx(low_fee_parent))
        node_mempool = _mempool(node)
        assert low_fee_parent.id.hex() not in node_mempool
        assert med_fee_child.id.hex() not in node_mempool

        # the child paying enough arrives, its input missing
        peer_sender.send_and_ping(_inv(high_fee_child))
        peer_sender.wait_for_getdata([high_fee_child.hash])
        peer_sender.send_and_ping(_tx(high_fee_child))

        # the parent is asked for again, refused alone and with a child as
        # it was, lest the child be censored
        clock.bump(_TXID_RELAY_DELAY)
        peer_sender.wait_for_getdata([low_fee_parent.id])

        # and taken in with that child, and not the other
        peer_sender.send_and_ping(_tx(low_fee_parent))
        node_mempool = _mempool(node)
        assert low_fee_parent.id.hex() in node_mempool
        assert high_fee_child.id.hex() in node_mempool
        assert med_fee_child.id.hex() not in node_mempool


def a_rejected_parent_is_taken_in_only_with_a_child_paying_enough(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_low_and_high_child(self.wallet)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _low_and_high_child(cluster, skip_counts, witness=True)


def a_rejected_parent_with_no_witness_is_taken_in_only_with_a_child_paying_enough(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_low_and_high_child(self.wallet_nonsegwit)`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _low_and_high_child(cluster, skip_counts, witness=False)


def parent_and_child_are_evaluated_together_only_from_one_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_orphan_consensus_failure`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(cluster, skip_counts, Capability.CLOCK, coins=1)
    clock = Clock(node)
    low_fee_parent = _below_mempoolminfee(node, wallet)
    # a child spending the parent, its witness invalid
    tx_orphan_bad_wit = malleated_to_invalid_witness(
        wallet.create_self_transfer(
            utxo_to_spend=_new_utxo(wallet, low_fee_parent), fee=_BAD_ORPHAN_FEE
        )
    )
    with ExitStack() as stack:
        bad_orphan_sender = RelayConn.inbound(stack, node)
        parent_sender = RelayConn.inbound(stack, node)

        # the child arrives first, its input missing
        bad_orphan_sender.send_and_ping(_inv(tx_orphan_bad_wit))
        clock.bump(_NONPREF_PEER_TX_DELAY)
        bad_orphan_sender.wait_for_getdata([tx_orphan_bad_wit.hash])
        bad_orphan_sender.send_and_ping(_tx(tx_orphan_bad_wit))

        # the parent is asked for by txid
        clock.bump(_NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY)
        bad_orphan_sender.wait_for_getdata([low_fee_parent.id])

        # another peer sends it, and the two are not evaluated together
        parent_sender.send_and_ping(_tx(low_fee_parent))
        node_mempool = _mempool(node)
        assert low_fee_parent.id.hex() not in node_mempool
        assert tx_orphan_bad_wit.id.hex() not in node_mempool

        # the child's own peer sends it, and neither peer is dropped
        bad_orphan_sender.send_and_ping(_tx(low_fee_parent))
        bad_orphan_sender.sync_with_ping()
        parent_sender.sync_with_ping()


def an_invalid_parent_from_another_peer_leaves_the_orphan(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_parent_consensus_failure`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(cluster, skip_counts, Capability.CLOCK, coins=1)
    clock = Clock(node)
    low_fee_parent = _below_mempoolminfee(node, wallet)
    high_fee_child = _child(
        wallet, low_fee_parent, 999 * _FEERATE_1SAT_VB, witness=True
    )
    # the parent, its witness invalid
    tx_parent_bad_wit = malleated_to_invalid_witness(low_fee_parent)
    with ExitStack() as stack:
        package_sender = RelayConn.inbound(stack, node)
        fake_parent_sender = RelayConn.inbound(stack, node)

        # the child arrives first, its input missing
        package_sender.send_and_ping(_inv(high_fee_child))
        clock.bump(_NONPREF_PEER_TX_DELAY)
        package_sender.wait_for_getdata([high_fee_child.hash])
        package_sender.send_and_ping(_tx(high_fee_child))

        # the parent is asked for by txid
        clock.bump(_NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY)
        package_sender.wait_for_getdata([tx_parent_bad_wit.id])

        # another peer sends the invalid one, refused alone for its fee
        fake_parent_sender.send_and_ping(_tx(tx_parent_bad_wit))
        node_mempool = _mempool(node)
        assert tx_parent_bad_wit.id.hex() not in node_mempool
        assert high_fee_child.id.hex() not in node_mempool

        # the child is kept, and taken in once its own peer sends the parent
        package_sender.send_and_ping(_inv(low_fee_parent))
        clock.bump(_NONPREF_PEER_TX_DELAY)
        package_sender.wait_for_getdata([low_fee_parent.hash])
        package_sender.send_and_ping(_tx(low_fee_parent))
        node_mempool = _mempool(node)
        assert low_fee_parent.id.hex() in node_mempool
        assert high_fee_child.id.hex() in node_mempool


def no_rejected_parent_of_a_two_parent_orphan_is_requested(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_multiple_parents`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, _, p2pk = _node(
        cluster,
        skip_counts,
        Capability.TYPED_OUTBOUND,
        Capability.CLOCK,
        coins=0,
        p2pk_coins=2,
    )
    clock = Clock(node)
    # two parents with no witness, each under the mempool's minimum fee rate
    parent_low_1 = _p2pk_below_mempoolminfee(node, p2pk[0])
    parent_low_2 = _p2pk_below_mempoolminfee(node, p2pk[1])
    child_bumping = p2pk_tx(
        [p2pk_new_utxo(parent_low_1), p2pk_new_utxo(parent_low_2)],
        999 * parent_low_1.vsize,
    )
    with ExitStack() as stack:
        peer_sender = RelayConn.outbound(stack, node)

        # both parents are sent unasked, and refused for their fee
        peer_sender.send_and_ping(_tx(parent_low_1))
        peer_sender.send_and_ping(_tx(parent_low_2))
        node_mempool = _mempool(node)
        assert parent_low_1.id.hex() not in node_mempool
        assert parent_low_2.id.hex() not in node_mempool

        # the child is sent, and neither parent asked for
        peer_sender.send_and_ping(_tx(child_bumping))
        clock.bump(_GETDATA_WAIT)
        peer_sender.sync_with_ping()
        assert not peer_sender.was_asked()


def an_orphan_is_taken_in_with_one_parent_beside_another_in_the_mempool(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_other_parent_in_mempool`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(
        cluster, skip_counts, Capability.TYPED_OUTBOUND, Capability.CLOCK, coins=2
    )
    clock = Clock(node)
    # a grandparent taken in by itself
    grandparent_high = wallet.create_self_transfer(
        fee_rate=10 * _FEERATE_1SAT_VB, confirmed_only=True
    )
    # a parent needing its child to pay for it
    parent_low = _below_mempoolminfee(node, wallet, _new_utxo(wallet, grandparent_high))
    # a parent taken in by itself, ahead of the child
    parent_high = wallet.create_self_transfer(
        fee_rate=10 * _FEERATE_1SAT_VB, confirmed_only=True
    )
    child = wallet.create_self_transfer_multi(
        utxos_to_spend=[_new_utxo(wallet, parent_high), _new_utxo(wallet, parent_low)],
        fee_per_output=999 * parent_low.vsize,
    )
    with ExitStack() as stack:
        peer_sender = RelayConn.outbound(stack, node)

        # the grandparent and the first parent are taken in
        peer_sender.send_and_ping(_tx(grandparent_high))
        assert grandparent_high.id.hex() in _mempool(node)
        peer_sender.send_and_ping(_tx(parent_high))
        assert parent_high.id.hex() in _mempool(node)

        # the child is kept as an orphan, and the other parent asked for
        peer_sender.send_and_ping(_tx(child))
        clock.bump(_NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY)
        peer_sender.wait_for_getdata([parent_low.id])
        peer_sender.send_and_ping(_tx(parent_low))

        node_mempool = _mempool(node)
        assert grandparent_high.id.hex() in node_mempool
        assert parent_high.id.hex() in node_mempool
        if _takes_two_parent_package(node):
            assert parent_low.id.hex() in node_mempool
            assert child.id.hex() in node_mempool
            return

        # a package of the other parent and the child is refused, sent by
        # the peer or submitted
        assert parent_low.id.hex() not in node_mempool
        assert child.id.hex() not in node_mempool
        result = node.rpc.call(
            "submitpackage",
            [[parent_low.serialize(True).hex(), child.serialize(True).hex()]],
        )
        assert result["package_msg"] == "package-not-child-with-unconfirmed-parents"


def a_package_is_taken_in_on_top_of_another(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_1p1c_on_1p1c`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(cluster, skip_counts, coins=1)
    # two generations of a parent and a child, the younger spending the older
    low_fee_great_grandparent = _below_mempoolminfee(node, wallet)
    high_fee_grandparent = _child(
        wallet, low_fee_great_grandparent, 20 * _FEERATE_1SAT_VB, witness=True
    )
    low_fee_parent = _below_mempoolminfee(
        node, wallet, _new_utxo(wallet, high_fee_grandparent)
    )
    high_fee_child = _child(wallet, low_fee_parent, 20 * _FEERATE_1SAT_VB, witness=True)
    with ExitStack() as stack:
        peer_sender = RelayConn.inbound(stack, node)

        # the pair spending the confirmed coin first, then the other
        for parent_relative, child_relative in (
            (low_fee_great_grandparent, high_fee_grandparent),
            (low_fee_parent, high_fee_child),
        ):
            # the child arrives first, its input missing
            peer_sender.send_and_ping(_inv(child_relative))
            peer_sender.wait_for_getdata([child_relative.hash])
            peer_sender.send_and_ping(_tx(child_relative))

            # the parent is asked for by txid, and taken in with the child
            peer_sender.wait_for_getdata([parent_relative.id])
            peer_sender.send_and_ping(_tx(parent_relative))

        node_mempool = _mempool(node)
        assert low_fee_great_grandparent.id.hex() in node_mempool
        assert high_fee_grandparent.id.hex() in node_mempool
        assert low_fee_parent.id.hex() in node_mempool
        assert high_fee_child.id.hex() in node_mempool
        entry = node.rpc.call("getmempoolentry", [low_fee_great_grandparent.id.hex()])
        assert entry["descendantcount"] == 4


def an_orphan_outlives_large_orphans_from_other_peers(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_orphanage_dos_large`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(cluster, skip_counts, Capability.CLOCK, coins=1)
    clock = Clock(node)
    large_orphans = [_large_orphan() for _ in range(_LARGE_ORPHANS)]

    rpc = client_per_call(node) if serves_through_libevent(node) else node.rpc
    # each is an orphan, and within the standard size an orphan is kept at
    for large_orphan in large_orphans:
        assert large_orphan.vsize <= _LARGE_VSIZE
        assert large_orphan.weight < _MAX_STANDARD_TX_WEIGHT
        assert 3 * large_orphan.vsize >= 2 * _LARGE_VSIZE
        (result,) = rpc.call(
            "testmempoolaccept", [[large_orphan.serialize(True).hex()]]
        )
        assert not result["allowed"]
        assert result["reject-reason"] == "missing-inputs"

    with ExitStack() as stack:
        peer_normal = RelayConn.inbound(stack, node)
        peer_doser = RelayConn.inbound(stack, node)

        # a large orphan from each of a set of peers, its parent asked for
        for large_orphan in large_orphans[:_INDIVIDUAL_DOSERS]:
            peer_doser_individual = RelayConn.inbound(stack, node)
            peer_doser_individual.send_and_ping(_tx(large_orphan))
            clock.bump(_NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY)
            peer_doser_individual.wait_for_getdata([large_orphan.vin[0].prev_out.tx_id])
        wait_until(lambda: len(_orphans(node)) == _INDIVIDUAL_DOSERS, timeout=_WAIT)

        # a child of a parent refused for its fee, from a peer of its own
        low_fee_parent = _below_mempoolminfee(node, wallet)
        high_fee_child = wallet.create_self_transfer(
            utxo_to_spend=_new_utxo(wallet, low_fee_parent),
            fee_rate=200 * _FEERATE_1SAT_VB,
            target_vsize=_LARGE_VSIZE,
        )

        # the child is kept as an orphan, and its parent asked for
        peer_normal.send_and_ping(_inv(high_fee_child))
        clock.bump(_NONPREF_PEER_TX_DELAY)
        peer_normal.wait_for_getdata([high_fee_child.hash])
        peer_normal.send_and_ping(_tx(high_fee_child))
        clock.bump(_NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY)
        peer_normal.wait_for_getdata([low_fee_parent.id])

        # the rest of the large orphans, from one peer, evict some orphan,
        # none where the bound is on their number alone and is not reached
        for large_orphan in large_orphans[_INDIVIDUAL_DOSERS:]:
            peer_doser.send_and_ping(_tx(large_orphan))
        if _bounds_orphans_by_count(node):
            assert len(large_orphans) + 1 <= _MAX_ORPHAN_TRANSACTIONS
            wait_until(
                lambda: len(_orphans(node)) == len(large_orphans) + 1, timeout=_WAIT
            )
        else:
            wait_until(
                lambda: len(_orphans(node)) < len(large_orphans) + 1, timeout=_WAIT
            )

        # the parent arrives, and is taken in with the child
        peer_normal.send_and_ping(_tx(low_fee_parent))
        entry = node.rpc.call("getmempoolentry", [high_fee_child.id.hex()])
        assert entry["ancestorcount"] == 2


def an_orphan_outlives_many_orphans_from_other_peers(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_orphanage_dos_many`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet, _ = _node(
        cluster, skip_counts, Capability.CLOCK, coins=1, inbound_relay_percent=True
    )
    clock = Clock(node)
    by_count = _bounds_orphans_by_count(node)
    with ExitStack() as stack:
        peer_normal = RelayConn.inbound(stack, node)

        # the same small orphans from each of a set of peers: the first sends
        # them, and the rest announce them, since a node records no
        # announcement for a peer sending a `tx` it already keeps as an orphan
        shared_orphans = [_small_orphan() for _ in range(_BATCH_SIZE)]
        peer_doser_shared = [
            RelayConn.inbound(stack, node) for _ in range(_NUM_PEERS_SHARED)
        ]
        for orphan in shared_orphans:
            peer_doser_shared[0].send(_tx(orphan))
        peer_doser_shared[0].sync_with_ping()
        shared_inv = Inv(
            [Inventory(InventoryType.MSG_WTX, tx.hash) for tx in shared_orphans]
        )
        for peer_doser in peer_doser_shared[1:]:
            peer_doser.send(shared_inv)
        for peer_doser in peer_doser_shared:
            peer_doser.sync_with_ping()
        wait_until(
            lambda: any(tx.id.hex() in _orphans(node) for tx in shared_orphans),
            timeout=_WAIT,
        )

        # where the bound is on each peer's share, their announcements fill
        # the orphanage to its bound
        if not by_count:
            assert _announcements(node) == _DEFAULT_MAX_ORPHANAGE_LATENCY_SCORE

        # a child of a parent refused for its fee, from a peer of its own
        low_fee_parent = _below_mempoolminfee(node, wallet)
        high_fee_child = _child(
            wallet, low_fee_parent, 200 * _FEERATE_1SAT_VB, witness=True
        )

        # the child is kept as an orphan, and its parent asked for
        peer_normal.send_and_ping(_inv(high_fee_child))
        clock.bump(_NONPREF_PEER_TX_DELAY)
        peer_normal.wait_for_getdata([high_fee_child.hash])
        peer_normal.send_and_ping(_tx(high_fee_child))
        wait_until(lambda: high_fee_child.id.hex() in _orphans(node), timeout=_WAIT)
        clock.bump(_NONPREF_PEER_TX_DELAY + _TXID_RELAY_DELAY)
        peer_normal.wait_for_getdata([low_fee_parent.id])

        # small orphans of its own from one more peer, some kept
        peer_doser_batch = RelayConn.inbound(stack, node)
        this_batch_orphans = [_small_orphan() for _ in range(_BATCH_SINGLE_DOSER)]
        for orphan in this_batch_orphans:
            peer_doser_batch.send(_tx(orphan))
        peer_doser_batch.sync_with_ping()
        wait_until(
            lambda: any(tx.id.hex() in _orphans(node) for tx in this_batch_orphans),
            timeout=_WAIT,
        )

        # where the bound is on their number alone, the orphanage is full,
        # and the child may be among the orphans evicted at random
        if by_count:
            wait_until(
                lambda: len(_orphans(node)) == _MAX_ORPHAN_TRANSACTIONS,
                timeout=_WAIT,
            )
            kept = high_fee_child.id.hex() in _orphans(node)

            # a kept child is taken in with its parent, an evicted one is not
            peer_normal.send_and_ping(_tx(low_fee_parent))
            assert (high_fee_child.id.hex() in _mempool(node)) == kept
            if kept:
                entry = node.rpc.call("getmempoolentry", [high_fee_child.id.hex()])
                assert entry["ancestorcount"] == 2
            return

        # the child is still kept
        assert high_fee_child.id.hex() in _orphans(node)

        # the parent arrives, and is taken in with the child
        peer_normal.send_and_ping(_tx(low_fee_parent))
        assert high_fee_child.id.hex() in _mempool(node)
        entry = node.rpc.call("getmempoolentry", [high_fee_child.id.hex()])
        assert entry["ancestorcount"] == 2
