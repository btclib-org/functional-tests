# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_compactblocks`, as bodies over each node.

Read from Core's `test/functional/p2p_compactblocks.py` (`b9788a5a10bd`,
2026-09-30): how a node negotiates, builds, serves and takes BIP152's
compact blocks, and which malformed or invalid ones drop the peer
sending them. Each of the checks below is one of Core's `test_*`
methods, a body over a fresh node:

- a node announces new blocks as `cmpctblock` only to a peer whose
  `sendcmpct` asks for it at version 2, and stops once one asks it not
  to, an inbound peer or an outbound one;
- a `cmpctblock` a node announces, or sends for a `getdata`, prefills
  its coinbase and carries every other transaction as the short id
  BIP152 derives from its wtxid;
- a block announced by `inv` or by `headers` is asked for as a compact
  one, and a `blocktxn` carrying what the node asks for completes it;
- a node asks only for the transactions of a `cmpctblock` it has not
  got, and asks for none once its mempool holds them all;
- a node answers a `getblocktxn` for a block near its tip with those
  transactions and one for a deeper block with the whole block, and
  drops a peer asking for an index past the block;
- a block deeper than Core's `MAX_CMPCTBLOCK_DEPTH` is sent whole where
  a compact one is asked for, and a `cmpctblock` for a block off the
  tip leaves only its header;
- a `cmpctblock` whose block has too little work to be kept leaves no
  header behind;
- a `blocktxn` that does not complete the block has the node ask for
  the whole of it;
- a block submitted over RPC is announced as `cmpctblock` to every peer
  asking for it;
- a `cmpctblock` carrying a block that fails validation keeps the peer,
  as does the same one sent again, and one building on that block drops
  it;
- a `getblocktxn` naming no index drops the peer;
- a second `blocktxn` for a block whose reconstruction failed drops the
  peer;
- a `sendcmpct` whose announce octet is neither zero nor one drops the
  peer;
- a `cmpctblock` prefilling an index past its block drops the peer,
  sent unasked by a high-bandwidth peer or asked for from a
  low-bandwidth one, and sent by a high-bandwidth peer for a block
  already in flight from as many peers as Core's
  `MAX_CMPCTBLOCKS_INFLIGHT_PER_BLOCK`, which the node reconstructs
  optimistically;
- a block a peer announces and does not complete is reconstructed from
  another peer's `cmpctblock`, and one whose coinbase witness the block
  does not commit to leaves the first peer's `blocktxn` to complete it;
- a node asks the second peer announcing a block for its transactions,
  and a third only where that one is outbound;
- `getpeerinfo` reports which side has selected the other for
  high-bandwidth mode;
- a node ignores a `cmpctblock` from a peer that has sent no
  `sendcmpct`, and one it did not ask for from a peer it has not
  selected for high-bandwidth mode.

Where Core's check reads the node's log, it is two bodies: a wire half,
asserting what the peer sees, and a log half asserting bitcoind's own
wording beside it (`Capability.DEBUG_LOG`).

Every body but those over a `sendcmpct`'s announce octet and an empty
`getblocktxn` mines its coins first (`Capability.MINE`,
`mini_wallet.MiniWallet`), which also leaves initial block download,
where a node neither asks for compact blocks nor announces them. The
bodies over an outbound peer ask for `Capability.TYPED_OUTBOUND` first,
`_BlockConn.outbound` having the node dial a `peer.Listener`. BIP152's
messages are btclib's own `btclib.p2p.compact_blocks`, and `_cmpctblock`
below builds one the way Core's `HeaderAndShortIDs.initialize_from_block`
does at `use_witness=True`.

Core's `TestP2PConn` records what the node sends from its framework's
network thread. Here `_BlockConn`, a `Conn` (`support/p2p_conns.py`), does so
on the test's own thread, where a wait reads the peer's connection: a
wait reads the peer up to the `pong` of a ping round trip ahead of every
poll, and a check that the node sent nothing reads it up to that `pong`
once.

What differs from Core's file besides:

- Core runs every check over one node and its peers, each check starting
  from the chain and the peer state the checks before it left; here each
  body starts a fresh node and builds that state itself. A body whose
  check needs the node to take a `cmpctblock` it did not ask for first
  has its peer send a `sendcmpct` and deliver a block, which makes it a
  high-bandwidth peer, as Core's earlier checks have made it: a node
  carrying bitcoin/bitcoin#32606 ignores such a `cmpctblock` from any
  other peer;
- Core fills its blocks with transactions paying and spending bare
  `OP_TRUE` scripts, which its node takes into the mempool only under the
  `-acceptnonstdtxn=1` it starts with; here `MiniWallet`'s own
  transactions fill them, and the node starts with no option;
- Core runs `test_sendcmpct` over each of its peers, an outbound one
  among them; here it runs over one inbound peer and over one outbound
  one, each a body of its own;
- Core's `test_getblocktxn_handler` reads the blocks its earlier checks
  left; here each block it reads carries a chain of transactions;
- the log line Core's `test_getblocktxn_handler` reads beside the
  disconnect for an index past the block is not asserted;
- Core's `test_invalid_cmpctblock_message` reads `getpeerinfo`'s
  `bip152_hb_to` and `bip152_hb_from` for its first two peers; here the
  `sendcmpct` the node sent each is read, and the peer's own request is
  not. Its check of the optimistic reconstruction is a body of its own,
  asking for `Capability.TYPED_OUTBOUND`, its outbound peer taking the
  last slot a block may be in flight from;
- Core's `test_invalid_tx_in_compactblock` adds the witness commitment
  its transactions do not need before dropping the coinbase's witness;
  here the transactions carry witnesses, so the block already commits to
  them and keeps its header once the coinbase's witness is dropped, and
  the block with an invalid transaction is built anew on the tip, where
  Core edits its own;
- Core's `test_multiple_blocktxn_response` waits after its first
  `blocktxn` for a `getdata` naming the block, which the one its header
  drew already does; here that one is cleared first, so the wait is for
  the node's fallback;
- the log line Core's `test_low_work_compactblocks` reads names its peer
  as `0`; here the id is `getpeerinfo`'s;
- Core's `test_compactblock_reconstruction_stalling_peer` gives the
  coinbase a witness its block carries no commitment for; here the block
  commits to its transactions' witnesses, so the coinbase's witness is
  given a reserved value other than the one the commitment was built
  with, and the stalling peer's `blocktxn` carries the witnesses, where
  Core's transactions have none;
- Core's stalling peer in
  `test_compactblock_reconstruction_parallel_reconstruction` has
  delivered blocks in its earlier checks; here it delivers none, so the
  node never selects it for high-bandwidth mode, and `getpeerinfo`'s
  `bip152_hb_to` is read in the order of the peers' ids rather than in
  the order `getpeerinfo` lists them;
- Core's `test_compact_blocks_ignored` reads its high-bandwidth peer off
  those its earlier checks left; here that peer is made high-bandwidth
  first, in the state Core's own
  `test_highbandwidth_mode_states_via_getpeerinfo` leaves its peer in.

A `sendcmpct` whose announce octet is neither zero nor one drops the
peer in `v32.0` and later, from
bitcoin/bitcoin@2d0dce0af54b8eb0ebdaf62f12a92d7e559a281e, and so does a
`getblocktxn` naming no index, from
bitcoin/bitcoin@28641fd195db2a175fd43fee2e32758aef9816a6. A `cmpctblock`
from a peer that has sent no `sendcmpct`, and one the node did not ask
for from a peer it has not selected for high-bandwidth mode, are ignored
there too, from bitcoin/bitcoin#32606. `v32.0rc1` is the first tag to
carry each. A bitcoind whose `getnetworkinfo` `version` reads older than
that
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35))
keeps the peer, answering the `getblocktxn` with a `blocktxn` carrying
no transaction, and takes each `cmpctblock`, which is asserted there
instead, the log halves reading the node's own lines for the exchange
and no `Misbehaving`. A `master` build between a change's merge and the
version's move to `32.99` reads older and does what the change has it do
all the same, the constants' own comments having the commits.

An invalid `cmpctblock` on the optimistic path drops the peer from
bitcoin/bitcoin#35321, which no tag up to `v32.1` carries. A bitcoind
whose `getnetworkinfo` `version` reads older than `32.99`'s keeps that
peer, which is asserted there instead. A `master` build at `32.99` from
before the change's merge keeps it too, and fails the check, its
constant's own comment having the commits.

Each `p2p_compactblocks_<node>_test.py` module beside this one runs each body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import random
from contextlib import AbstractContextManager, ExitStack, nullcontext
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, override

from bitcoin_core_rpc import magic_from_chain
from btclib.block.block import Block
from btclib.block.mining import mine
from btclib.p2p import (
    BlockPayload,
    BlockTxn,
    CmpctBlock,
    GetBlockTxn,
    GetData,
    GetHeaders,
    Headers,
    Inv,
    Inventory,
    InventoryType,
    Message,
    PrefilledTransaction,
    SendCmpct,
    SendHeaders,
    TxPayload,
)
from btclib.script.script import serialize as script_serialize
from btclib.script.witness import Witness
from btclib.tx import Tx
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.mini_wallet import MiniWallet, build_next_block
from btclib_functional_tests.peer import Peer
from tests.integration.support.p2p_conns import Conn

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from btclib.p2p import Payload, Version

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.mini_wallet import Utxo
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_block_off_the_tip_is_not_sent_compact",
    "a_compact_block_is_built_as_bip152_says",
    "a_low_work_cmpctblock_is_ignored",
    "a_low_work_cmpctblock_is_logged",
    "a_second_blocktxn_drops_the_peer",
    "a_second_blocktxn_is_logged",
    "a_stalling_peer_leaves_the_block_to_another",
    "a_submitted_block_is_announced_compact",
    "a_wrong_blocktxn_falls_back_to_the_block",
    "an_announced_block_is_asked_for_compact",
    "an_empty_getblocktxn_drops_the_peer",
    "an_empty_getblocktxn_is_logged",
    "an_invalid_cmpctblock_drops_the_peer",
    "an_invalid_optimistic_cmpctblock_drops_the_peer",
    "an_invalid_sendcmpct_announce_drops_the_peer",
    "an_invalid_sendcmpct_announce_is_logged",
    "getblocktxn_is_answered_near_the_tip",
    "getpeerinfo_reports_high_bandwidth_states",
    "invalid_transactions_in_a_cmpctblock_keep_the_peer",
    "only_missing_transactions_are_asked_for",
    "sendcmpct_negotiates_compact_announcements",
    "sendcmpct_negotiates_over_an_outbound_peer",
    "the_last_reconstruction_is_kept_for_an_outbound_peer",
    "unsolicited_cmpctblocks_are_ignored",
]

type _Cluster = Callable[[int], Sequence[NodeAdapter]]

_MAGIC = magic_from_chain("regtest")

# the wait most of Core's own waits here pass
_SHORT_WAIT = 30.0

# Core's own constants (`p2p_compactblocks.py`)
_MAX_GETBLOCKTXN_DEPTH = 10
_MAX_CMPCTBLOCK_DEPTH = 5

# Core's own count of transactions `test_compactblock_construction` sends
_CONSTRUCTION_TRANSACTIONS = 25

# the transactions each block `getblocktxn_is_answered_near_the_tip`
# reads carries, beside its coinbase
_HANDLER_CHAIN_LENGTH = 3

# Core's own `CMPCTBLOCKS_VERSION`, and the versions on either side of it
_VERSION = 2

# Core's own depth `test_low_work_compactblocks` builds its block at
_LOW_WORK_DEPTH = 150

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which a `sendcmpct` whose
# announce octet is neither zero nor one drops the peer: `v32.0`'s,
# `v32.0rc1` being the first tag carrying the change. A known limit: a
# `master` build from its merge (`d84fc352cb`, 2026-06-23) until the
# version moved to `32.99` (`f3fec67c3e`, 2026-09-11) reports `319900` and
# drops the peer all the same, so this test fails against such a build
_REFUSES_ANNOUNCE_OCTET_VERSION = 320000

# Core's own `CLIENT_VERSION`, at or past which a `getblocktxn` naming no
# index drops the peer: `v32.0`'s, `v32.0rc1` being the first tag carrying
# the change. A known limit: a `master` build from its merge
# (`975a314667`, 2026-08-04) until the version moved to `32.99`
# (`f3fec67c3e`, 2026-09-11) reports `319900` and drops the peer all the
# same, so this test fails against such a build
_REFUSES_EMPTY_GETBLOCKTXN_VERSION = 320000

# Core's own `CLIENT_VERSION`, at or past which a `cmpctblock` the node did
# not ask for is ignored from a peer it has not selected for high-bandwidth
# mode, and any `cmpctblock` from a peer that has sent no `sendcmpct`:
# `v32.0`'s, `v32.0rc1` being the first tag carrying bitcoin/bitcoin#32606.
# A known limit: a `master` build from its merge (`69fc991791`,
# 2026-07-06) until the version moved to `32.99` (`f3fec67c3e`,
# 2026-09-11) reports `319900` and ignores them all the same, so this test
# fails against such a build
_IGNORES_UNSOLICITED_CMPCTBLOCK_VERSION = 320000

# Core's own `CLIENT_VERSION`, at or past which an invalid `cmpctblock`
# on the optimistic reconstruction path drops the peer: `32.99`'s, no
# tag up to `v32.1` carrying bitcoin/bitcoin#35321. A known limit: a
# `master` build from the version's move to `32.99` (`f3fec67c3e`,
# 2026-09-11) until that change's merge (`0a8e904cee`, 2026-10-20)
# reports `329900` and keeps the peer, so this test fails against such a
# build
_REFUSES_OPTIMISTIC_CMPCTBLOCK_VERSION = 329900


class _BlockConn(Conn):
    """Core's own `TestP2PConn`: a peer recording the blocks announced to it.

    `announced` is the hash of every block a `cmpctblock`, a `headers` or
    an `inv` named, Core's `announced_blockhashes`, and `block_announced`
    whether one has since `clear_block_announcement`. What the node sent
    last of each command is `Peer.last_message`, Core's `last_message`.
    An `inv` is not asked for.
    """

    def __init__(self, peer: Peer, version: Version) -> None:
        super().__init__(peer, version)
        self.announced: set[bytes] = set()
        self.block_announced = False

    @override
    def send(self, payload: Payload) -> None:
        """Core's own `send_without_ping`."""
        self.peer.send(payload, check_validity=False)

    @override
    def _handle(self, message: Message) -> None:
        """Core's `on_cmpctblock`, `on_headers`, `on_inv`, and `on_ping`."""
        super()._handle(message)
        if message.command == "cmpctblock":
            self.block_announced = True
            self.announced.add(_parse_cmpctblock(message).header.hash)
        elif message.command == "headers":
            self.block_announced = True
            headers = Headers.parse(message.payload, check_validity=False).headers
            self.announced.update(header.hash for header in headers)
        elif message.command == "inv":
            for item in Inv.parse(message.payload).items:
                if item.type_code == InventoryType.MSG_BLOCK:
                    self.block_announced = True
                    self.announced.add(item.hash)

    @override
    def wait_until(
        self, predicate: Callable[[], bool], *, timeout: float = _SHORT_WAIT
    ) -> None:
        """`Conn.wait_until`, this file's shorter wait by default."""
        super().wait_until(predicate, timeout=timeout)

    def has(self, command: str) -> bool:
        """Whether the node has sent a `command` since it was last cleared."""
        return command in self.peer.last_message

    def last(self, command: str) -> Message:
        """Core's own `last_message[command]`."""
        return self.peer.last_message[command]

    def clear(self, *commands: str) -> None:
        """Forget the last message of each of `commands`."""
        for command in commands:
            self.peer.last_message.pop(command, None)

    def clear_block_announcement(self) -> None:
        """Core's own `clear_block_announcement`."""
        self.block_announced = False
        self.clear("inv", "headers", "cmpctblock")

    def request_headers_and_sync(self, tip: bytes) -> None:
        """Core's own `request_headers_and_sync(locator=[tip])`."""
        self.clear_block_announcement()
        self.send(GetHeaders(locator=[tip]))
        self.wait_until(lambda: self.block_announced)
        self.clear_block_announcement()

    def wait_for_block_announcement(self, block_hash: bytes) -> None:
        """Core's own `wait_for_block_announcement`."""
        self.wait_until(lambda: block_hash in self.announced)

    def wait_for_getdata(self, block_hash: bytes) -> None:
        """Core's own `wait_for_getdata([block_hash])`: the latest names it."""

        def _asked() -> bool:
            if not self.has("getdata"):
                return False
            items = GetData.parse(self.last("getdata").payload).items
            return [item.hash for item in items] == [block_hash]

        self.wait_until(_asked)


def _parse_cmpctblock(message: Message) -> CmpctBlock:
    return CmpctBlock.parse(message.payload, check_validity=False)


def _cmpctblock(block: Block, prefill: Sequence[int] = (0,)) -> CmpctBlock:
    """Core's `HeaderAndShortIDs.initialize_from_block`, `use_witness=True`.

    The transactions at `prefill` are sent whole, and every other one as
    the short id of its wtxid, under the nonce Core's own leaves at zero.
    """
    keyed = CmpctBlock(block.header, check_validity=False)
    return CmpctBlock(
        block.header,
        keyed.nonce,
        [
            keyed.short_id(tx.hash)
            for index, tx in enumerate(block.transactions)
            if index not in prefill
        ],
        [
            PrefilledTransaction(index, block.transactions[index], check_validity=False)
            for index in prefill
        ],
        check_validity=False,
    )


def _tx(tx: Tx) -> TxPayload:
    """Core's own `msg_tx(tx)`, its witness serialized."""
    return TxPayload(tx, include_witness=True, check_validity=False)


def _block_payload(block: Block) -> BlockPayload:
    """Core's own `msg_block(block)`, witnesses included."""
    return BlockPayload(block, include_witness=True, check_validity=False)


def _tip(node: NodeAdapter) -> bytes:
    """Return the node's own `getbestblockhash`, in display order."""
    return bytes.fromhex(node.rpc.call("getbestblockhash"))


def _mempool(node: NodeAdapter) -> list[str]:
    """Return the node's own `getrawmempool`."""
    txids = node.rpc.call("getrawmempool")
    assert isinstance(txids, list)
    return txids


def _read_block(node: NodeAdapter, block_hash: bytes) -> Block:
    """Return the node's own block at `block_hash`, read over `getblock`."""
    block_hex = node.rpc.call("getblock", [block_hash.hex(), 0])
    # the header's own check defaults to mainnet's proof-of-work limit
    return Block.parse(bytes.fromhex(block_hex), check_validity=False)


def _node(
    cluster: _Cluster, skip_counts: SkipCounts, coins: int, *capabilities: Capability
) -> tuple[NodeAdapter, MiniWallet]:
    """Return a fresh node out of initial block download, and its wallet.

    :param coins: the matured coins the body spends.
    :param capabilities: what the body asks for besides `Capability.MINE`,
        asked for first.
    """
    (node,) = cluster(1)
    for capability in capabilities:
        require(capability, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + coins)
    return node, wallet


def _hb_states(node: NodeAdapter) -> list[tuple[bool, bool]]:
    """Core's own `assert_highbandwidth_states`, read for every peer.

    Each peer's `getpeerinfo` `bip152_hb_to` and `bip152_hb_from`, in the
    order the node gave the peers their ids, which is the order they
    connected in.
    """
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return [
        (peer["bip152_hb_to"], peer["bip152_hb_from"])
        for peer in sorted(peers, key=lambda peer: peer["id"])
    ]


def _chain_block(
    node: NodeAdapter, wallet: MiniWallet, utxo: Utxo, count: int
) -> tuple[Block, Utxo]:
    """Core's own `build_block_with_transactions`, and the coin it leaves.

    A block on the node's tip carrying a chain of `count` transactions
    from `utxo`, each spending the one before it, unsubmitted; the coin
    is the last transaction's own, Core's `self.utxos.append`.
    """
    chain = wallet.create_self_transfer_chain(chain_length=count, utxo_to_spend=utxo)
    block = build_next_block(node, wallet.script_pub_key, chain)
    return block, wallet.new_utxos(chain[-1])[0]


def _request_cb_announcements(conn: _BlockConn, node: NodeAdapter) -> None:
    """Core's own `request_cb_announcements`.

    The `getheaders` for the tip is what has the node count the peer as
    holding the tip's header, which is what it announces a child of as a
    `cmpctblock` on.
    """
    conn.send(GetHeaders(locator=[_tip(node)]))
    conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))


def _make_high_bandwidth(
    conn: _BlockConn, node: NodeAdapter, wallet: MiniWallet
) -> None:
    """Core's own `make_peer_hb_to_candidate`, the node's selection asserted.

    The peer delivers a block on the tip, which has a node that the peer
    has sent a `sendcmpct` select it for high-bandwidth mode, the node's
    own `sendcmpct` asking it to announce new blocks as `cmpctblock`.
    The wallet is resynced to the tip the block moves.
    """
    block = build_next_block(node, wallet.script_pub_key)
    conn.send_and_ping(_block_payload(block))
    assert _tip(node) == block.header.hash
    assert SendCmpct.parse(conn.last("sendcmpct").payload).announce is True
    wallet.resync()


def _getblocktxn_expected(
    conn: _BlockConn, block_hash: bytes, indexes: Sequence[int] | None = None
) -> None:
    """Core's own `getblocktxn_expected`: the node asked for `indexes`."""
    assert conn.has("getblocktxn")
    request = GetBlockTxn.parse(conn.last("getblocktxn").payload)
    assert request.block_hash == block_hash
    if indexes is not None:
        assert list(request.indexes) == list(indexes)


def _sendcmpct(conn: _BlockConn, node: NodeAdapter, wallet: MiniWallet) -> None:
    """Core's own `test_sendcmpct`, over `conn`."""
    # the node's own sendcmpct offers version 2
    conn.wait_until(lambda: conn.has("sendcmpct"))
    assert SendCmpct.parse(conn.last("sendcmpct").payload).version == _VERSION

    tip = _tip(node)

    def check_announcement_of_new_block(predicate: Callable[[], bool]) -> None:
        conn.clear_block_announcement()
        (block_hash,) = wallet.generate(1)
        conn.wait_for_block_announcement(block_hash)
        assert conn.block_announced
        assert predicate()

    def no_cmpctblock() -> bool:
        return not conn.has("cmpctblock")

    # no block is announced as a cmpctblock yet
    check_announcement_of_new_block(no_cmpctblock)

    # nor after the peer asks for headers
    conn.request_headers_and_sync(tip)
    check_announcement_of_new_block(lambda: no_cmpctblock() and conn.has("inv"))

    # a sendcmpct asking at a version below or above 2 is ignored
    conn.request_headers_and_sync(tip)
    conn.send_and_ping(SendCmpct(announce=True, version=_VERSION - 1))
    check_announcement_of_new_block(no_cmpctblock)

    conn.request_headers_and_sync(tip)
    conn.send_and_ping(SendCmpct(announce=True, version=_VERSION + 1))
    check_announcement_of_new_block(no_cmpctblock)

    # as is one at version 2 not asking to be announced to
    conn.request_headers_and_sync(tip)
    conn.send_and_ping(SendCmpct(announce=False, version=_VERSION))
    check_announcement_of_new_block(no_cmpctblock)

    # one at version 2 asking to be is announced to, block after block
    conn.request_headers_and_sync(tip)
    conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
    check_announcement_of_new_block(lambda: conn.has("cmpctblock"))
    check_announcement_of_new_block(lambda: conn.has("cmpctblock"))

    # after a sendheaders too
    conn.send_and_ping(SendHeaders())
    check_announcement_of_new_block(lambda: conn.has("cmpctblock"))

    # and after a version 1 sendcmpct not asking to be
    conn.send_and_ping(SendCmpct(announce=False, version=_VERSION - 1))
    check_announcement_of_new_block(lambda: conn.has("cmpctblock"))

    # until a version 2 one asks not to be, headers announcing instead
    conn.send_and_ping(SendCmpct(announce=False, version=_VERSION))
    check_announcement_of_new_block(lambda: no_cmpctblock() and conn.has("headers"))


def sendcmpct_negotiates_compact_announcements(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_sendcmpct`, over an inbound peer.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0)
    with ExitStack() as stack:
        _sendcmpct(_BlockConn.inbound(stack, node), node, wallet)


def sendcmpct_negotiates_over_an_outbound_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_sendcmpct`, over an outbound full-relay peer.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0, Capability.TYPED_OUTBOUND)
    with ExitStack() as stack:
        _sendcmpct(_BlockConn.outbound(stack, node), node, wallet)


def _check_compactblock_construction(
    cmpct: CmpctBlock, block_hash: bytes, block: Block
) -> None:
    """Core's own `check_compactblock_construction_from_block`."""
    assert cmpct.header.hash == block_hash

    # the coinbase is prefilled, and each prefilled transaction is the
    # block's own, its witness included
    assert len(cmpct.prefilled_txns) >= 1
    assert cmpct.prefilled_txns[0].index == 0
    for entry in cmpct.prefilled_txns:
        assert entry.tx.id == block.transactions[entry.index].id
        assert entry.tx.hash == block.transactions[entry.index].hash

    # every transaction is announced, and every short id is BIP152's
    assert cmpct.tx_count == len(block.transactions)
    prefilled = {entry.index for entry in cmpct.prefilled_txns}
    short_ids = iter(cmpct.short_ids)
    for index, tx in enumerate(block.transactions):
        if index not in prefilled:
            assert next(short_ids) == cmpct.short_id(tx.hash)


def a_compact_block_is_built_as_bip152_says(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_compactblock_construction`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, _CONSTRUCTION_TRANSACTIONS)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        wallet.generate(1)

        txs = [wallet.send_self_transfer() for _ in range(_CONSTRUCTION_TRANSACTIONS)]
        # the check is not broken: a transaction carries a witness
        assert any(tx.is_segwit for tx in txs)

        conn.wait_for_block_announcement(_tip(node))
        _request_cb_announcements(conn, node)

        # the block mined next is announced as a cmpctblock
        conn.clear_block_announcement()
        (block_hash,) = wallet.generate(1, confirm=txs)
        block = _read_block(node, block_hash)
        conn.wait_until(lambda: conn.has("cmpctblock"))
        _check_compactblock_construction(
            _parse_cmpctblock(conn.last("cmpctblock")), block_hash, block
        )

        # and sent as one for a getdata
        conn.clear_block_announcement()
        conn.send(GetData([Inventory(InventoryType.MSG_CMPCT_BLOCK, block_hash)]))
        conn.wait_until(lambda: conn.has("cmpctblock"))
        _check_compactblock_construction(
            _parse_cmpctblock(conn.last("cmpctblock")), block_hash, block
        )


def an_announced_block_is_asked_for_compact(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_compactblock_requests`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))

        for announce in ("inv", "header"):
            block = build_next_block(node, wallet.script_pub_key)
            block_hash = block.header.hash
            headers = Headers([block.header], check_validity=False)
            if announce == "inv":
                conn.send(Inv([Inventory(InventoryType.MSG_BLOCK, block_hash)]))
                conn.wait_until(lambda: conn.has("getheaders"))
            conn.send(headers)
            conn.wait_for_getdata(block_hash)
            items = GetData.parse(conn.last("getdata").payload).items
            assert items[0].type_code == InventoryType.MSG_CMPCT_BLOCK

            # a cmpctblock leaving the coinbase out, as a short id
            coinbase = block.transactions[0]
            keyed = CmpctBlock(block.header, 0, check_validity=False)
            cmpct = CmpctBlock(
                block.header, 0, [keyed.short_id(coinbase.hash)], check_validity=False
            )
            conn.clear("getblocktxn")
            conn.send_and_ping(cmpct)
            assert _tip(node) == block.header.previous_block_hash
            # is completed by the coinbase the node asks for
            _getblocktxn_expected(conn, block_hash, [0])

            conn.send_and_ping(BlockTxn(block_hash, [coinbase], check_validity=False))
            assert _tip(node) == block_hash


def only_missing_transactions_are_asked_for(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_getblocktxn_requests`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
        _make_high_bandwidth(conn, node, wallet)

        def getblocktxn_response(cmpct: CmpctBlock, indexes: Sequence[int]) -> None:
            conn.clear("getblocktxn")
            conn.send_and_ping(cmpct)
            _getblocktxn_expected(conn, cmpct.header.hash, indexes)

        def tip_after_message(payload: Payload, block_hash: bytes) -> None:
            conn.send_and_ping(payload)
            assert _tip(node) == block_hash

        # a cmpctblock not reconstructed: every transaction is asked for
        block, utxo = _chain_block(node, wallet, wallet.get_utxo(), 5)
        block_hash = block.header.hash
        getblocktxn_response(_cmpctblock(block), [1, 2, 3, 4, 5])
        tip_after_message(
            BlockTxn(block_hash, block.transactions[1:], check_validity=False),
            block_hash,
        )

        # the prefilled transactions interspersed
        block, utxo = _chain_block(node, wallet, utxo, 5)
        block_hash = block.header.hash
        getblocktxn_response(_cmpctblock(block, [0, 1, 5]), [2, 3, 4])
        tip_after_message(
            BlockTxn(block_hash, block.transactions[2:5], check_validity=False),
            block_hash,
        )

        # one transaction given ahead: the one not in the mempool is asked
        block, utxo = _chain_block(node, wallet, utxo, 5)
        block_hash = block.header.hash
        conn.send_and_ping(_tx(block.transactions[1]))
        assert block.transactions[1].id.hex() in _mempool(node)
        getblocktxn_response(_cmpctblock(block, [0, 2, 3, 4]), [5])
        tip_after_message(
            BlockTxn(block_hash, [block.transactions[5]], check_validity=False),
            block_hash,
        )

        # every transaction given ahead: the block is reconstructed at once
        block, _ = _chain_block(node, wallet, utxo, 10)
        for tx in block.transactions[1:]:
            conn.send(_tx(tx))
        conn.sync_with_ping()
        node_mempool = _mempool(node)
        for tx in block.transactions[1:]:
            assert tx.id.hex() in node_mempool
        conn.clear("getblocktxn")
        tip_after_message(_cmpctblock(block), block.header.hash)
        assert not conn.has("getblocktxn")


def getblocktxn_is_answered_near_the_tip(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_getblocktxn_handler`, the disconnect's log line aside.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, _MAX_GETBLOCKTXN_DEPTH + 1)
    # each block the requests below read carries transactions
    for _ in range(_MAX_GETBLOCKTXN_DEPTH + 1):
        chain = wallet.send_self_transfer_chain(chain_length=_HANDLER_CHAIN_LENGTH)
        wallet.generate(1, confirm=chain)

    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        chain_height = node.rpc.call("getblockcount")
        current_height = chain_height
        while current_height >= chain_height - _MAX_GETBLOCKTXN_DEPTH:
            block_hash = bytes.fromhex(node.rpc.call("getblockhash", [current_height]))
            block = _read_block(node, block_hash)
            count = len(block.transactions)
            indexes = sorted(random.sample(range(count), random.randint(1, count)))
            conn.send(GetBlockTxn(block_hash, indexes))
            conn.wait_until(lambda: conn.has("blocktxn"), timeout=10)

            answer = BlockTxn.parse(conn.last("blocktxn").payload, check_validity=False)
            assert answer.block_hash == block_hash
            assert len(answer.transactions) == len(indexes)
            for index, tx in zip(indexes, answer.transactions, strict=True):
                assert tx.id == block.transactions[index].id
                # the witness is the block's own
                assert tx.hash == block.transactions[index].hash
            conn.clear("blocktxn")
            current_height -= 1

        # past the depth, the whole block is sent instead
        block_hash = bytes.fromhex(node.rpc.call("getblockhash", [current_height]))
        conn.clear("block", "blocktxn")
        conn.send_and_ping(GetBlockTxn(block_hash, [0]))
        assert conn.has("block")
        sent = Block.parse(conn.last("block").payload, check_validity=False)
        assert sent.header.hash == block_hash
        assert not conn.has("blocktxn")

        # an index past the block has the node drop the peer
        bad_peer = _BlockConn.inbound(stack, node)
        block_hash = bytes.fromhex(node.rpc.call("getblockhash", [chain_height]))
        count = len(_read_block(node, block_hash).transactions)
        bad_peer.send(GetBlockTxn(block_hash, [count]))
        bad_peer.peer.wait_for_disconnect()


def a_block_off_the_tip_is_not_sent_compact(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_compactblocks_not_at_tip`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
        _make_high_bandwidth(conn, node, wallet)

        # a block at Core's MAX_CMPCTBLOCK_DEPTH is sent compact
        new_blocks = []
        for _ in range(_MAX_CMPCTBLOCK_DEPTH + 1):
            conn.clear_block_announcement()
            new_blocks += wallet.generate(1)
            conn.wait_until(lambda: conn.block_announced)

        conn.clear_block_announcement()
        conn.send(GetData([Inventory(InventoryType.MSG_CMPCT_BLOCK, new_blocks[0])]))
        conn.wait_until(lambda: conn.has("cmpctblock"))

        # one block deeper, the whole block is sent instead
        conn.clear_block_announcement()
        wallet.generate(1)
        conn.wait_until(lambda: conn.block_announced)
        conn.clear_block_announcement()
        conn.clear("block")
        conn.send(GetData([Inventory(InventoryType.MSG_CMPCT_BLOCK, new_blocks[0])]))
        conn.wait_until(lambda: conn.has("block"))
        sent = Block.parse(conn.last("block").payload, check_validity=False)
        assert sent.header.hash == new_blocks[0]

        # a cmpctblock for a block off the tip leaves its header alone
        cur_height = node.rpc.call("getblockcount")
        parent = bytes.fromhex(node.rpc.call("getblockhash", [cur_height - 5]))
        on_tip = build_next_block(node, wallet.script_pub_key)
        header = mine(replace(on_tip.header, previous_block_hash=parent))
        assert header is not None
        block = Block(header, on_tip.transactions, check_validity=False)
        conn.send_and_ping(_cmpctblock(block))

        (tip,) = [
            tip
            for tip in node.rpc.call("getchaintips")
            if tip["hash"] == block.header.hash.hex()
        ]
        assert tip["status"] == "headers-only"

        # and a getblocktxn for it is left unanswered
        conn.clear("blocktxn")
        conn.send_and_ping(GetBlockTxn(block.header.hash, [0]))
        assert not conn.has("blocktxn")


def a_wrong_blocktxn_falls_back_to_the_block(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_incorrect_blocktxn_response`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
        _make_high_bandwidth(conn, node, wallet)

        block, _ = _chain_block(node, wallet, wallet.get_utxo(), 10)
        block_hash = block.header.hash
        # the first five of its transactions are relayed ahead
        for tx in block.transactions[1:6]:
            conn.send(_tx(tx))
        conn.sync_with_ping()
        node_mempool = _mempool(node)
        for tx in block.transactions[1:6]:
            assert tx.id.hex() in node_mempool

        conn.clear("getblocktxn")
        conn.send_and_ping(_cmpctblock(block))
        _getblocktxn_expected(conn, block_hash, [6, 7, 8, 9, 10])

        # a blocktxn carrying a wrong transaction
        wrong = [block.transactions[5], *block.transactions[7:]]
        conn.send_and_ping(BlockTxn(block_hash, wrong, check_validity=False))
        assert _tip(node) == block.header.previous_block_hash

        # has the node ask for the whole block, which it then takes
        conn.wait_for_getdata(block_hash)
        items = GetData.parse(conn.last("getdata").payload).items
        assert items[0].type_code in (
            InventoryType.MSG_BLOCK,
            InventoryType.MSG_WITNESS_BLOCK,
        )
        conn.send_and_ping(_block_payload(block))
        assert _tip(node) == block_hash


def a_submitted_block_is_announced_compact(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_end_to_end_block_relay`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1)
    with ExitStack() as stack:
        listeners = [_BlockConn.inbound(stack, node) for _ in range(2)]
        for listener in listeners:
            _request_cb_announcements(listener, node)

        block, _ = _chain_block(node, wallet, wallet.get_utxo(), 10)
        for listener in listeners:
            listener.clear_block_announcement()
        block_hex = block.serialize(include_witness=True, check_validity=False).hex()
        assert node.rpc.call("submitblock", [block_hex]) is None

        for listener in listeners:
            listener.wait_until(partial(listener.has, "cmpctblock"))
            cmpct = _parse_cmpctblock(listener.last("cmpctblock"))
            assert cmpct.header.hash == block.header.hash


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
    log_path: Path | None,
    expected: Sequence[str],
    unexpected: Sequence[str] = (),
) -> AbstractContextManager[None]:
    """Return `assert_debug_log` over `log_path`, or nothing for a wire half."""
    if log_path is None:
        return nullcontext()
    return assert_debug_log(log_path, expected, unexpected)


def _carries(node: NodeAdapter, version: int) -> bool:
    """Whether `node` does what Core's check asserts of Core's `master`.

    Core's own claim for every node, bitcoind before `version` excepted:
    that build does what the check's own body asserts there instead, read
    off its own `getnetworkinfo` `version`
    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
    """
    if not isinstance(node, BitcoindAdapter):
        return True
    known: int = node.rpc.call("getnetworkinfo")["version"]
    return known >= version


def _sendcmpct_announcing(octet: int) -> bytes:
    """Core's own `msg_sendcmpct(announce=octet, version=2)`, framed.

    `SendCmpct.announce` is a `bool`, so an octet past one is written
    here.
    """
    payload = bytes([octet]) + _VERSION.to_bytes(8, byteorder="little")
    message = Message(_MAGIC, "sendcmpct", payload, check_validity=False)
    return message.serialize(check_validity=False)


def _invalid_sendcmpct_announce(node: NodeAdapter, log_path: Path | None) -> None:
    """Core's own `test_invalid_sendcmpct_announce`, over `node`.

    :param log_path: the node's own log where the check is a log half,
        `None` where it is a wire half.
    """
    refuses = _carries(node, _REFUSES_ANNOUNCE_OCTET_VERSION)
    with ExitStack() as stack:
        bad_peer = _BlockConn.inbound(stack, node)
        if refuses:
            with _expecting(log_path, ["invalid sendcmpct announce field"]):
                bad_peer.peer.send_raw(_sendcmpct_announcing(2))
                bad_peer.peer.wait_for_disconnect()
        else:
            # an older build reads the octet as a bool, and keeps the peer
            with _expecting(
                log_path, ["received: sendcmpct (9 bytes)"], ["Misbehaving"]
            ):
                bad_peer.peer.send_raw(_sendcmpct_announcing(2))
                bad_peer.sync_with_ping()


def an_invalid_sendcmpct_announce_drops_the_peer(cluster: _Cluster) -> None:
    """Check the wire half of Core's own `test_invalid_sendcmpct_announce`.

    :param cluster: a node's own cluster fixture.
    """
    (node,) = cluster(1)
    _invalid_sendcmpct_announce(node, None)


def an_invalid_sendcmpct_announce_is_logged(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the log half of Core's own `test_invalid_sendcmpct_announce`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    _invalid_sendcmpct_announce(node, _debug_log(node, skip_counts))


def an_invalid_cmpctblock_drops_the_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_invalid_cmpctblock_message`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0)
    with ExitStack() as stack:
        # a high-bandwidth peer
        hb_peer = _BlockConn.inbound(stack, node)
        _request_cb_announcements(hb_peer, node)
        _make_high_bandwidth(hb_peer, node, wallet)

        # and a low-bandwidth one
        lb_peer = _BlockConn.inbound(stack, node)
        _request_cb_announcements(lb_peer, node)
        assert SendCmpct.parse(lb_peer.last("sendcmpct").payload).announce is False

        # a cmpctblock prefilling its one transaction at index 1
        block = build_next_block(node, wallet.script_pub_key)
        prefilled = PrefilledTransaction(1, block.transactions[0], check_validity=False)
        cmpct = CmpctBlock(block.header, 0, [], [prefilled], check_validity=False)

        # drops the high-bandwidth peer sending it unasked
        hb_peer.send(cmpct)
        hb_peer.peer.wait_for_disconnect()
        assert _tip(node) == block.header.previous_block_hash

        # and the low-bandwidth peer the node asked for it
        lb_peer.send(Headers([block.header], check_validity=False))
        lb_peer.wait_for_getdata(block.header.hash)
        lb_peer.send(cmpct)
        lb_peer.peer.wait_for_disconnect()
        assert _tip(node) == block.header.previous_block_hash


def an_invalid_optimistic_cmpctblock_drops_the_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_invalid_cmpctblock_message`, its optimistic path.

    A block is in flight from a low-bandwidth peer, a high-bandwidth
    inbound one and the outbound one, every slot Core's
    `MAX_CMPCTBLOCKS_INFLIGHT_PER_BLOCK` allows; a fourth peer's invalid
    `cmpctblock` for it is read on the optimistic path.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0, Capability.TYPED_OUTBOUND)
    refuses = _carries(node, _REFUSES_OPTIMISTIC_CMPCTBLOCK_VERSION)
    with ExitStack() as stack:
        outbound_peer = _BlockConn.outbound(stack, node)
        _request_cb_announcements(outbound_peer, node)
        stall1 = _BlockConn.inbound(stack, node)
        stall2 = _BlockConn.inbound(stack, node)
        bad_peer = _BlockConn.inbound(stack, node)
        for conn in (stall1, stall2, bad_peer):
            _request_cb_announcements(conn, node)

        # the outbound peer, stall2 and bad_peer high-bandwidth, stall1 not
        for conn in (outbound_peer, stall2, bad_peer):
            _make_high_bandwidth(conn, node, wallet)
        assert _hb_states(node)[1:] == [(False, True), (True, True), (True, True)]

        # the block's coinbase as a short id, which no mempool holds
        block = build_next_block(node, wallet.script_pub_key)
        block_hash = block.header.hash
        inflight = _cmpctblock(block, prefill=())

        # stall1 announces it solicited, and every slot is taken
        stall1.send(Headers([block.header], check_validity=False))
        stall1.wait_for_getdata(block_hash)
        for conn in (stall1, stall2, outbound_peer):
            conn.clear("getblocktxn")
            conn.send_and_ping(inflight)
            _getblocktxn_expected(conn, block_hash, [0])

        # a cmpctblock prefilling its one transaction at index 1
        prefilled = PrefilledTransaction(1, block.transactions[0], check_validity=False)
        cmpct = CmpctBlock(block.header, 0, [], [prefilled], check_validity=False)
        if refuses:
            bad_peer.send(cmpct)
            bad_peer.peer.wait_for_disconnect()
        else:
            # an older build ignores it, and keeps the peer
            bad_peer.send_and_ping(cmpct)
        assert _tip(node) == block.header.previous_block_hash

        # stall1 completes the block
        stall1.send_and_ping(
            BlockTxn(block_hash, [block.transactions[0]], check_validity=False)
        )
        assert _tip(node) == block_hash


def _multiple_blocktxn(
    node: NodeAdapter, wallet: MiniWallet, log_path: Path | None
) -> None:
    """Core's own `test_multiple_blocktxn_response`, over `node`.

    :param log_path: the node's own log where the check is a log half,
        `None` where it is a wire half.
    """
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        conn.send_and_ping(SendCmpct(announce=False, version=_VERSION))

        block, _ = _chain_block(node, wallet, wallet.get_utxo(), 2)
        block_hash = block.header.hash
        # the header has the node ask for the block
        conn.send(Headers([block.header], check_validity=False))
        conn.wait_for_getdata(block_hash)

        conn.clear("getblocktxn")
        conn.send_and_ping(_cmpctblock(block))
        _getblocktxn_expected(conn, block_hash, [1, 2])

        # a blocktxn failing the reconstruction has it ask for the block
        wrong = BlockTxn(
            block_hash,
            [block.transactions[2], block.transactions[1]],
            check_validity=False,
        )
        conn.clear("getdata")
        conn.send_and_ping(wrong)
        assert _tip(node) == block.header.previous_block_hash
        conn.wait_for_getdata(block_hash)
        items = GetData.parse(conn.last("getdata").payload).items
        assert items[0].type_code in (
            InventoryType.MSG_BLOCK,
            InventoryType.MSG_WITNESS_BLOCK,
        )

        # and the same blocktxn again drops the peer
        with _expecting(
            log_path, ["previous compact block reconstruction attempt failed"]
        ):
            conn.send(wrong)
            conn.peer.wait_for_disconnect()


def a_second_blocktxn_drops_the_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half of Core's own `test_multiple_blocktxn_response`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1)
    _multiple_blocktxn(node, wallet, None)


def a_second_blocktxn_is_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half of Core's own `test_multiple_blocktxn_response`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1)
    _multiple_blocktxn(node, wallet, _debug_log(node, skip_counts))


def _prefilled_whole(block: Block) -> CmpctBlock:
    """Core's own `initialize_from_block` prefilling every transaction."""
    return _cmpctblock(block, range(len(block.transactions)))


def invalid_transactions_in_a_cmpctblock_keep_the_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_invalid_tx_in_compactblock`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
        _make_high_bandwidth(conn, node, wallet)

        block, _ = _chain_block(node, wallet, wallet.get_utxo(), 5)
        # the coinbase's witness dropped, the witness commitment kept
        coinbase = block.transactions[0]
        assert coinbase.vin[0].script_witness.stack
        stripped = Tx(
            version=coinbase.version,
            lock_time=coinbase.lock_time,
            vin=[replace(coinbase.vin[0], script_witness=Witness([]))],
            vout=list(coinbase.vout),
            check_validity=False,
        )
        block = Block(
            block.header, [stripped, *block.transactions[1:]], check_validity=False
        )
        conn.send_and_ping(_prefilled_whole(block))
        assert _tip(node) != block.header.hash

        # a transaction of the block made invalid, the commitment rebuilt
        txs = list(block.transactions[1:])
        txs[3] = Tx(
            version=txs[3].version,
            lock_time=txs[3].lock_time,
            vin=[replace(txs[3].vin[0], script_sig=script_serialize(["OP_RETURN"]))],
            vout=list(txs[3].vout),
            check_validity=False,
        )
        block = build_next_block(node, wallet.script_pub_key, txs)
        conn.send_and_ping(_prefilled_whole(block))
        assert _tip(node) != block.header.hash

        # sent again, the node's cached failure answers it
        conn.send_and_ping(_prefilled_whole(block))
        assert _tip(node) != block.header.hash

        # and a block building on it drops the peer
        header = mine(replace(block.header, previous_block_hash=block.header.hash))
        assert header is not None
        child = Block(header, block.transactions, check_validity=False)
        conn.send(_prefilled_whole(child))
        conn.peer.wait_for_disconnect()


def _empty_getblocktxn(node: NodeAdapter, log_path: Path | None) -> None:
    """Core's own `test_empty_getblocktxn_disconnects`, over `node`.

    :param log_path: the node's own log where the check is a log half,
        `None` where it is a wire half.
    """
    refuses = _carries(node, _REFUSES_EMPTY_GETBLOCKTXN_VERSION)
    with ExitStack() as stack:
        peer = _BlockConn.inbound(stack, node)
        block_hash = _tip(node)
        request = GetBlockTxn(block_hash, [])
        if refuses:
            with _expecting(
                log_path, ["getblocktxn received with no transaction indexes"]
            ):
                peer.send(request)
                peer.peer.wait_for_disconnect()
        else:
            # an older build answers it with no transaction, and keeps the peer
            with _expecting(
                log_path,
                ["received: getblocktxn (33 bytes)", "sending blocktxn (33 bytes)"],
                ["Misbehaving"],
            ):
                peer.send_and_ping(request)
            answer = BlockTxn.parse(peer.last("blocktxn").payload, check_validity=False)
            assert answer.block_hash == block_hash
            assert len(answer.transactions) == 0


def an_empty_getblocktxn_drops_the_peer(cluster: _Cluster) -> None:
    """Check the wire half of Core's own `test_empty_getblocktxn_disconnects`.

    :param cluster: a node's own cluster fixture.
    """
    (node,) = cluster(1)
    _empty_getblocktxn(node, None)


def an_empty_getblocktxn_is_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half of Core's own `test_empty_getblocktxn_disconnects`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    _empty_getblocktxn(node, _debug_log(node, skip_counts))


def _low_work_cmpctblock(
    node: NodeAdapter, wallet: MiniWallet, log_path: Path | None
) -> None:
    """Core's own `test_low_work_compactblocks`, over `node`.

    :param log_path: the node's own log where the check is a log half,
        `None` where it is a wire half.
    """
    wallet.generate(_LOW_WORK_DEPTH)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))

        # a block on the one Core's depth below the tip
        height = node.rpc.call("getblockcount")
        parent = bytes.fromhex(
            node.rpc.call("getblockhash", [height - _LOW_WORK_DEPTH])
        )
        on_tip = build_next_block(node, wallet.script_pub_key)
        header = mine(replace(on_tip.header, previous_block_hash=parent))
        assert header is not None
        block = Block(header, on_tip.transactions, check_validity=False)

        expected = []
        if log_path is not None:
            (peer_info,) = node.rpc.call("getpeerinfo")
            expected = [
                f"[net] Ignoring low-work compact block from peer {peer_info['id']}"
            ]
        with _expecting(log_path, expected):
            conn.send_and_ping(_cmpctblock(block))

        # leaves no header behind
        tips = [tip["hash"] for tip in node.rpc.call("getchaintips")]
        assert block.header.hash.hex() not in tips


def a_low_work_cmpctblock_is_ignored(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Check the wire half of Core's own `test_low_work_compactblocks`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0)
    _low_work_cmpctblock(node, wallet, None)


def a_low_work_cmpctblock_is_logged(cluster: _Cluster, skip_counts: SkipCounts) -> None:
    """Check the log half of Core's own `test_low_work_compactblocks`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0)
    _low_work_cmpctblock(node, wallet, _debug_log(node, skip_counts))


def _announce_cmpct_block(
    conn: _BlockConn,
    node: NodeAdapter,
    wallet: MiniWallet,
    utxo: Utxo,
    count: int,
    *,
    solicit: bool = False,
) -> tuple[Block, Utxo]:
    """Core's own `announce_cmpct_block`, and the coin its block leaves.

    A block on the tip carrying a chain of `count` transactions from
    `utxo`, sent by `conn` as a `cmpctblock` the node then asks the
    transactions of; with `solicit`, its header is sent first and the
    `cmpctblock` is the one the node asks for.
    """
    block, utxo = _chain_block(node, wallet, utxo, count)
    block_hash = block.header.hash
    if solicit:
        conn.send(Headers([block.header], check_validity=False))
        conn.wait_for_getdata(block_hash)
    conn.clear("getblocktxn")
    conn.send_and_ping(_cmpctblock(block))
    _getblocktxn_expected(conn, block_hash)
    assert _tip(node) != block_hash
    return block, utxo


def _blocktxn(block: Block) -> BlockTxn:
    """Core's own `msg_blocktxn` carrying every transaction but the coinbase."""
    return BlockTxn(block.header.hash, block.transactions[1:], check_validity=False)


def a_stalling_peer_leaves_the_block_to_another(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_compactblock_reconstruction_stalling_peer`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1)
    with ExitStack() as stack:
        stalling_peer = _BlockConn.inbound(stack, node)
        delivery_peer = _BlockConn.inbound(stack, node)
        for conn in (stalling_peer, delivery_peer):
            conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
            _make_high_bandwidth(conn, node, wallet)

        # a block the stalling peer announces and does not complete
        block, utxo = _announce_cmpct_block(
            stalling_peer, node, wallet, wallet.get_utxo(), 5
        )
        for tx in block.transactions[1:]:
            delivery_peer.send(_tx(tx))
        delivery_peer.sync_with_ping()
        node_mempool = _mempool(node)
        for tx in block.transactions[1:]:
            assert tx.id.hex() in node_mempool

        # is reconstructed from the delivery peer's own cmpctblock
        delivery_peer.send_and_ping(_cmpctblock(block))
        assert _tip(node) == block.header.hash

        # a cmpctblock whose coinbase witness the block does not commit to
        block, _ = _announce_cmpct_block(stalling_peer, node, wallet, utxo, 5)
        for tx in block.transactions[1:]:
            delivery_peer.send(_tx(tx))
        delivery_peer.sync_with_ping()

        coinbase = block.transactions[0]
        (reserved,) = coinbase.vin[0].script_witness.stack
        assert reserved == bytes(32)
        mutated = Tx(
            version=coinbase.version,
            lock_time=coinbase.lock_time,
            vin=[
                replace(coinbase.vin[0], script_witness=Witness([b"\x01" + bytes(31)]))
            ],
            vout=list(coinbase.vout),
            check_validity=False,
        )
        cmpct = _cmpctblock(
            Block(
                block.header, [mutated, *block.transactions[1:]], check_validity=False
            )
        )
        delivery_peer.send_and_ping(cmpct)
        assert _tip(node) != block.header.hash

        # leaves the stalling peer's own blocktxn to complete it
        stalling_peer.send_and_ping(_blocktxn(block))
        assert _tip(node) == block.header.hash


def getpeerinfo_reports_high_bandwidth_states(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_highbandwidth_mode_states_via_getpeerinfo`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 0)
    with ExitStack() as stack:
        conn = _BlockConn.inbound(stack, node)
        # neither side has selected the other yet
        assert _hb_states(node) == [(False, False)]

        # the peer asks to be announced to as a high-bandwidth peer
        conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
        assert _hb_states(node) == [(False, True)]

        # the block it delivers has the node select it
        block = build_next_block(node, wallet.script_pub_key)
        conn.send_and_ping(_block_payload(block))
        assert _tip(node) == block.header.hash
        assert _hb_states(node) == [(True, True)]

        # and it asks to be announced to as a low-bandwidth one
        conn.send_and_ping(SendCmpct(announce=False, version=_VERSION))
        assert _hb_states(node) == [(True, False)]


def the_last_reconstruction_is_kept_for_an_outbound_peer(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_compactblock_reconstruction_parallel_reconstruction`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 1, Capability.TYPED_OUTBOUND)
    with ExitStack() as stack:
        stalling_peer = _BlockConn.inbound(stack, node)
        stalling_peer.send_and_ping(SendCmpct(announce=True, version=_VERSION))
        delivery_peer = _BlockConn.inbound(stack, node)
        inbound_peer = _BlockConn.inbound(stack, node)
        outbound_peer = _BlockConn.outbound(stack, node)

        # every peer but the stalling one made high-bandwidth, each then
        # completing a block it announces
        utxo = wallet.get_utxo()
        for conn in (delivery_peer, inbound_peer, outbound_peer):
            conn.send_and_ping(SendCmpct(announce=True, version=_VERSION))
            _make_high_bandwidth(conn, node, wallet)
            block, utxo = _announce_cmpct_block(conn, node, wallet, utxo, 1)
            conn.send_and_ping(_blocktxn(block))
            assert _tip(node) == block.header.hash
            conn.clear("getblocktxn")

        for num_missing in (1, 5, 20):
            for conn in (delivery_peer, inbound_peer, outbound_peer):
                conn.clear("getblocktxn")
            assert [hb_to for hb_to, _ in _hb_states(node)] == [
                False,
                True,
                True,
                True,
            ]

            # the stalling peer announces first, asked for the block
            block, utxo = _announce_cmpct_block(
                stalling_peer, node, wallet, utxo, num_missing, solicit=True
            )
            block_hash = block.header.hash
            cmpct = _cmpctblock(block)

            # the second peer to announce it is asked for its transactions
            delivery_peer.send_and_ping(cmpct)
            _getblocktxn_expected(delivery_peer, block_hash)

            # a third, inbound, is not
            inbound_peer.send_and_ping(cmpct)
            assert not inbound_peer.has("getblocktxn")
            assert _tip(node) != block_hash

            # and a third, outbound, is
            outbound_peer.send_and_ping(cmpct)
            _getblocktxn_expected(outbound_peer, block_hash)

            # the second peer completes the block
            delivery_peer.send_and_ping(_blocktxn(block))
            assert _tip(node) == block_hash

            # and the stalling peer's late blocktxn keeps it
            stalling_peer.send_and_ping(_blocktxn(block))


def unsolicited_cmpctblocks_are_ignored(
    cluster: _Cluster, skip_counts: SkipCounts
) -> None:
    """Core's own `test_compact_blocks_ignored`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node, wallet = _node(cluster, skip_counts, 5)
    ignores = _carries(node, _IGNORES_UNSOLICITED_CMPCTBLOCK_VERSION)
    with ExitStack() as stack:
        # a high-bandwidth peer, not asking to be announced to
        hb_peer = _BlockConn.inbound(stack, node)
        hb_peer.send_and_ping(SendCmpct(announce=False, version=_VERSION))
        _make_high_bandwidth(hb_peer, node, wallet)

        # and a peer that has sent no sendcmpct
        peer = _BlockConn.inbound(stack, node)
        assert _hb_states(node) == [(True, False), (False, False)]

        def ignores_compact_block(conn: _BlockConn, *, solicited: bool) -> bool:
            """Core's own `ignores_compact_block`: no `getblocktxn` came."""
            block, _ = _chain_block(node, wallet, wallet.get_utxo(), 10)
            conn.clear("getblocktxn")
            if solicited:
                conn.send(Headers([block.header], check_validity=False))
                conn.wait_for_getdata(block.header.hash)
            conn.send_and_ping(_cmpctblock(block))
            return not conn.has("getblocktxn")

        # a cmpctblock from it is ignored, unasked and asked for; an older
        # build takes it either way
        assert ignores_compact_block(peer, solicited=False) is ignores
        assert ignores_compact_block(peer, solicited=True) is ignores

        # its sendcmpct leaves it a low-bandwidth peer
        peer.send_and_ping(SendCmpct(announce=False, version=_VERSION))
        assert _hb_states(node) == [(True, False), (False, False)]

        # whose cmpctblock is ignored unasked, and taken asked for
        assert ignores_compact_block(peer, solicited=False) is ignores
        assert _hb_states(node) == [(True, False), (False, False)]
        assert not ignores_compact_block(peer, solicited=True)
        assert _hb_states(node) == [(True, False), (False, False)]

        # and the high-bandwidth peer's is taken unasked
        assert not ignores_compact_block(hb_peer, solicited=False)
