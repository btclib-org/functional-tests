# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_sendheaders`, one body over each node.

Read from Core's `test/functional/p2p_sendheaders.py` (`a7acf277dc39`,
2026-10-02), a file needing no mechanism the adapter lacks
([ISS 317](https://github.com/btclib-org/functional-tests/issues/317)):
a node announces a new block to a peer by `inv` until the peer sends
`sendheaders`, and by `headers` after it. Two nodes are linked as
Core's `setup_network` links them (`Capability.CONNECT`), the first
mining (`Capability.GENERATE`) and the second mining the chains that
reorganise it (`Capability.INVALIDATE_BLOCK`). The first moves its
clock past Core's `HEADERS_RESPONSE_TIME` (`Capability.CLOCK`). Two
peers connect to the first node: one that only ever reads `inv`s, and
one without `NODE_NETWORK`, whose announcements are the subject.

- A `getheaders` with no locator answers the header its stop hash
  names only where that block is validated.
- Before `sendheaders`, every block is announced by `inv`, whatever
  the peer asked for or announced in between.
- After it, every block is announced by its header, whether the peer
  announced blocks of its own by `inv` or by `headers` in between. The
  same announcement from the other peer causes no second `getdata`, and
  the blocks it announced are not announced back to it.
- A reorganisation connecting no more than Core's
  `MAX_BLOCKS_TO_ANNOUNCE` blocks is announced by their headers, and a
  longer one by an `inv` of the tip. Announcements then stay `inv`s
  until the peer sends a `getheaders` for the tip or announces it, a
  `getdata` or an older `getheaders` not restoring them.
- Headers leading to as much work as the tip or more are fetched at
  once, up to Core's `MAX_BLOCKS_IN_TRANSIT_PER_PEER` blocks in flight.
  Headers leading to less work are not, and neither are blocks already
  stored.
- A header that does not connect is answered with a `getheaders`, and
  the headers sent in answer are fetched. Every further such header is
  answered with a `getheaders` too.
- A header that does not connect is no answer to the `getheaders` in
  flight: one more, with no empty `headers` before it, is answered with
  no `getheaders` until `HEADERS_RESPONSE_TIME` has passed.

The peer reading `inv`s is never asked for a block.

What differs from Core's file:

- Core's `P2PInterface` records what the node sends from its
  framework's network thread; here `_Conn` does it on the test's own
  thread, a wait reading the connection until its condition holds;
- Core checks that the node answered an `inv` of a block it lacks with
  no second `getheaders`, and that the peer reading `inv`s was never
  asked for a block, on whatever its network thread has read so far;
  here each is checked once the node answers a `ping` sent after it
  (`Peer.sync_with_ping`), so that each reads every message the node
  sent before;
- each block is built with btclib in the shape of Core's own
  `create_block` and `create_coinbase` (`blocktools.py`), its coinbase
  paying `OP_TRUE`.

Core's file at `v32.1` differs from the pin only in lacking the last
step above, and at `v30.3` also in handing each `create_block` a
coinbase of its own `create_coinbase` rather than a height, and in
writing its `test_function` predicates as lambdas. Every release this
suite runs answers that step as the pin does: an unconnecting header
stopped counting as an answer with bitcoin/bitcoin@9f66ac7cf193, first
in `v28.0`.

Each `p2p_sendheaders_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from datetime import UTC, datetime
from typing import TYPE_CHECKING, override

from bitcoin_core_rpc import magic_from_chain
from btclib.block.block import Block
from btclib.block.build import build_block, build_coinbase
from btclib.block.mining import mine
from btclib.block.proof_of_work import REGTEST_POW_LIMIT_BITS
from btclib.consensus import CONSENSUS_PARAMS
from btclib.p2p import (
    BlockPayload,
    GetBlocks,
    GetData,
    GetHeaders,
    Headers,
    Inv,
    Inventory,
    InventoryType,
    SendHeaders,
    ServiceFlags,
)

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import connect_nodes, sync_all
from btclib_functional_tests.peer import Peer
from btclib_functional_tests.timeout_factor import scaled
from tests.integration.p2p_conns_test import Conn

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.p2p import Message, Payload, Version

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["blocks_are_announced_by_headers_after_sendheaders"]

type _Node = NodeAdapter

_MAGIC = magic_from_chain("regtest")

_HALVING_INTERVAL = CONSENSUS_PARAMS["regtest"].subsidy_halving_interval

# Core's own `PRIV_KEYS` addresses (`test_framework/test_node.py`), the
# one each node's `get_deterministic_priv_key` answers, by its own index
_ADDRESSES = (
    "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z",
    "msX6jQXvxiNhx3Q62PKeLPrhrqZQdSimTg",
)

# what Core's own `create_coinbase` pays
_OP_TRUE = b"\x51"

# Core's own `create_block` default, `VERSIONBITS_LAST_OLD_BLOCK_VERSION`
_BLOCK_VERSION = 4

# Core's own default wait, `P2PInterface`'s and `wait_until`'s
_WAIT = 60.0

# Core's own `sync_blocks` and `sync_mempools` default timeout
_SYNC_TIMEOUT = 60.0

# Core's own `DIRECT_FETCH_RESPONSE_TIME`
_DIRECT_FETCH_RESPONSE_TIME = 0.05

# Core's own `NUM_HEADERS`
_NUM_HEADERS = 100

# Core's own `HEADERS_RESPONSE_TIME`, in seconds (`net_processing.cpp`)
_HEADERS_RESPONSE_TIME = 2 * 60

# Core's own `P2P_SERVICES`, what `add_p2p_connection` offers by default
_P2P_SERVICES = ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS

# `getheaders`' own "as much as you will give me"
_NO_STOP = bytes(32)


class _Conn(Conn):
    """Core's own `BaseNode`, a `P2PInterface` recording announcements.

    What the node sent last of each command is `Peer.last_message`, Core's
    `last_message`. A message is sent without `check_validity`, and a wait
    reads the connection until its condition holds, without `Conn`'s ping
    between reads.

    :param peer: the connection, its handshake done.
    :param version: the node's own `version`.
    """

    def __init__(self, peer: Peer, version: Version) -> None:
        super().__init__(peer, version)
        self.block_announced = False
        self.last_blockhash_announced: bytes | None = None
        self.recent_headers_announced: list[bytes] = []

    @override
    def send(self, payload: Payload) -> None:
        """Core's own `send_without_ping`."""
        self.peer.send(payload, check_validity=False)

    @override
    def _handle(self, message: Message) -> None:
        """Core's `BaseNode.on_inv` and `on_headers`, and `Conn`'s `on_ping`."""
        super()._handle(message)
        if message.command == "inv":
            self.block_announced = True
            self.last_blockhash_announced = Inv.parse(message.payload).items[-1].hash
        elif message.command == "headers":
            if headers := Headers.parse(message.payload, check_validity=False).headers:
                self.block_announced = True
                # headers may be announced over several messages
                self.recent_headers_announced += [header.hash for header in headers]
                self.last_blockhash_announced = headers[-1].hash

    @override
    def wait_until(
        self, predicate: Callable[[], bool], *, timeout: float = _WAIT
    ) -> None:
        """Core's own `P2PInterface.wait_until`: read until `predicate` holds.

        :raises ConnectionError: the node closed the connection.
        :raises TimeoutError: the wait ran out before `predicate` held.
        """
        deadline = time.monotonic() + scaled(timeout)
        while not predicate():
            self._receive(deadline)

    def has(self, command: str) -> bool:
        """Whether `command` is in Core's own `last_message`."""
        return command in self.peer.last_message

    def clear(self, *commands: str) -> None:
        """Forget the last message of each of `commands`."""
        for command in commands:
            self.peer.last_message.pop(command, None)

    def send_get_data(self, block_hashes: Sequence[bytes]) -> None:
        """Core's own `send_get_data`."""
        items = [Inventory(InventoryType.MSG_BLOCK, x) for x in block_hashes]
        self.send(GetData(items))

    def send_get_headers(self, locator: Sequence[bytes], hashstop: bytes) -> None:
        """Core's own `send_get_headers`."""
        self.send(GetHeaders(locator=locator, hash_stop=hashstop))

    def send_block_inv(self, blockhash: bytes) -> None:
        """Core's own `send_block_inv`."""
        self.send(Inv([Inventory(InventoryType.MSG_BLOCK, blockhash)]))

    def send_header_for_blocks(self, new_blocks: Sequence[Block]) -> None:
        """Core's own `send_header_for_blocks`."""
        self.send(Headers([block.header for block in new_blocks], check_validity=False))

    def send_getblocks(self, locator: Sequence[bytes]) -> None:
        """Core's own `send_getblocks`."""
        self.send(GetBlocks(locator=locator))

    def send_block(self, block: Block) -> None:
        """Core's own `send_without_ping(msg_block(block))`."""
        self.send(BlockPayload(block, include_witness=True, check_validity=False))

    def wait_for_block_announcement(self, block_hash: bytes) -> None:
        """Core's own `wait_for_block_announcement`."""
        self.wait_until(lambda: self.last_blockhash_announced == block_hash)

    def clear_block_announcements(self) -> None:
        """Core's own `clear_block_announcements`."""
        self.block_announced = False
        self.clear("inv", "headers")
        self.recent_headers_announced = []

    def check_last_headers_announcement(self, headers: Sequence[bytes]) -> None:
        """Core's own: the headers announced since the last clear are these."""
        self.wait_until(lambda: len(self.recent_headers_announced) >= len(headers))
        assert self.recent_headers_announced == list(headers)
        self.block_announced = False
        self.clear("headers")
        self.recent_headers_announced = []

    def check_last_inv_announcement(self, inv: Sequence[bytes]) -> None:
        """Core's own: the last announcement was an `inv` of `inv`."""
        self.wait_until(lambda: self.block_announced)
        compare_inv = []
        if self.has("inv"):
            items = Inv.parse(self.peer.last_message["inv"].payload).items
            compare_inv = [item.hash for item in items]
        assert compare_inv == list(inv)
        self.block_announced = False
        self.clear("inv")

    def wait_for_block(self, blockhash: bytes) -> None:
        """Core's own `wait_for_block`: the last `block` is `blockhash`'s."""

        def _received() -> bool:
            if not self.has("block"):
                return False
            payload = self.peer.last_message["block"].payload
            block = BlockPayload.parse(payload, check_validity=False).block
            return block.header.hash == blockhash

        self.wait_until(_received)

    def wait_for_getdata(
        self, hash_list: Sequence[bytes], *, timeout: float = _WAIT
    ) -> None:
        """Core's own `wait_for_getdata`: the last `getdata` is `hash_list`."""

        def _asked() -> bool:
            if not self.has("getdata"):
                return False
            items = GetData.parse(self.peer.last_message["getdata"].payload).items
            return [item.hash for item in items] == list(hash_list)

        self.wait_until(_asked, timeout=timeout)

    def wait_for_getheaders(self, block_hash: bytes) -> None:
        """Core's own `wait_for_getheaders(block_hash)`, popping as it reads."""

        def _asked() -> bool:
            message = self.peer.last_message.pop("getheaders", None)
            if message is None:
                return False
            return GetHeaders.parse(message.payload).locator[0] == block_hash

        self.wait_until(_asked)


def _connect(
    stack: ExitStack, node: NodeAdapter, services: ServiceFlags = _P2P_SERVICES
) -> _Conn:
    """Core's own `add_p2p_connection(BaseNode(), services=services)`."""
    peer = stack.enter_context(Peer(node.p2p_address, _MAGIC))
    conn = _Conn(peer, peer.handshake(services=services))
    conn.sync_with_ping()
    return conn


def _block(previous: bytes, height: int, block_time: int) -> Block:
    """Core's own `create_block(previous, height=, ntime=)`, solved."""
    coinbase = build_coinbase(height, _OP_TRUE, halving_interval=_HALVING_INTERVAL)
    candidate = build_block(
        previous,
        [coinbase],
        datetime.fromtimestamp(block_time, UTC),
        REGTEST_POW_LIMIT_BITS,
        version=_BLOCK_VERSION,
    )
    solved = mine(candidate.header)
    assert solved is not None
    return Block(solved, candidate.transactions, check_validity=False)


def _best(node: NodeAdapter) -> bytes:
    """Return `getbestblockhash`'s own answer."""
    return bytes.fromhex(str(node.rpc.call("getbestblockhash")))


def _count(node: NodeAdapter) -> int:
    """Return `getblockcount`'s own answer."""
    count = node.rpc.call("getblockcount")
    assert isinstance(count, int)
    return count


def _getblock(node: NodeAdapter, block_hash: bytes) -> dict[str, object]:
    """Return `getblock`'s own answer."""
    answer = node.rpc.call("getblock", [block_hash.hex()])
    assert isinstance(answer, dict)
    return answer


def _block_time(node: NodeAdapter) -> int:
    """Return the `time` of the node's own tip."""
    block_time = _getblock(node, _best(node))["time"]
    assert isinstance(block_time, int)
    return block_time


class _Network:
    """Core's own `SendHeadersTest`: two linked nodes and the first's peers.

    :param nodes: the node under test, and the one creating reorgs.
    """

    def __init__(self, nodes: Sequence[NodeAdapter]) -> None:
        self.nodes = nodes
        self.p2ps: list[_Conn] = []

    def generatetoaddress(self, index: int, count: int) -> list[bytes]:
        """Core's own `generatetoaddress`, then its `sync_all`.

        :param index: the node that mines, paying its own `PRIV_KEYS` address.
        """
        hashes = self.nodes[index].rpc.call(
            "generatetoaddress", [count, _ADDRESSES[index]]
        )
        assert isinstance(hashes, list)
        sync_all(self.nodes, timeout=_SYNC_TIMEOUT)
        return [bytes.fromhex(block_hash) for block_hash in hashes]

    def mine_blocks(self, count: int) -> bytes:
        """Core's own `mine_blocks`: mine `count` blocks, return the new tip."""
        for conn in self.p2ps:
            conn.clear_block_announcements()
        self.generatetoaddress(0, count)
        return _best(self.nodes[0])

    def mine_reorg(self, length: int) -> list[bytes]:
        """Core's own `mine_reorg`: replace `length` blocks with one more.

        :return: the hashes of the blocks the second node mined.
        """
        node0, node1 = self.nodes
        # every block the reorg takes off is the first node's
        self.generatetoaddress(0, length)
        for conn in self.p2ps:
            conn.wait_for_block_announcement(_best(node0))
            conn.clear_block_announcements()

        tip_height = _count(node1)
        hash_to_invalidate = node1.rpc.call("getblockhash", [tip_height - (length - 1)])
        node1.rpc.call("invalidateblock", [hash_to_invalidate])
        return self.generatetoaddress(1, length + 1)


def _test_null_locators(net: _Network, test_node: _Conn, inv_node: _Conn) -> None:
    """Core's own `test_null_locators`."""
    node0 = net.nodes[0]
    (tip_hash,) = net.generatetoaddress(0, 1)
    tip = node0.rpc.call("getblockheader", [tip_hash.hex()])
    assert isinstance(tip, dict)

    inv_node.check_last_inv_announcement(inv=[tip_hash])
    test_node.check_last_inv_announcement(inv=[tip_hash])

    # a null locator and a validated stop hash: the header is sent
    test_node.clear_block_announcements()
    test_node.send_get_headers(locator=[], hashstop=tip_hash)
    test_node.check_last_headers_announcement(headers=[tip_hash])

    # a null locator and a stop hash not validated: no header
    block = _block(tip_hash, tip["height"] + 1, tip["mediantime"] + 1)
    test_node.send_header_for_blocks([block])
    test_node.clear_block_announcements()
    test_node.send_get_headers(locator=[], hashstop=block.header.hash)
    test_node.sync_with_ping()
    assert test_node.block_announced is False
    inv_node.clear_block_announcements()
    test_node.send_block(block)
    inv_node.check_last_inv_announcement(inv=[block.header.hash])


def _part_1(net: _Network, test_node: _Conn, inv_node: _Conn) -> int:
    """Core's own part 1: no headers announcement before `sendheaders`.

    :return: Core's own `block_time` at the part's end.
    """
    node0 = net.nodes[0]
    tip = _best(node0)
    block_time = 0
    for i in range(4):
        old_tip = tip
        tip = net.mine_blocks(1)
        inv_node.check_last_inv_announcement(inv=[tip])
        test_node.check_last_inv_announcement(inv=[tip])
        # none of these answers changes the next announcement
        if i == 0:
            # the block alone
            test_node.send_get_data([tip])
            test_node.wait_for_block(tip)
        elif i == 1:
            # its header and the block
            test_node.send_get_headers(locator=[old_tip], hashstop=tip)
            test_node.send_get_data([tip])
            test_node.wait_for_block(tip)
            test_node.clear_block_announcements()
        elif i == 2:
            # a block of the peer's own, announced by its header
            inv_node.clear_block_announcements()
            height = _count(node0)
            block_time = _block_time(node0) + 1
            new_block = _block(tip, height + 1, block_time)
            test_node.send_header_for_blocks([new_block])
            test_node.wait_for_getdata([new_block.header.hash])
            test_node.send_block(new_block)
            test_node.sync_with_ping()
            inv_node.wait_until(lambda: inv_node.block_announced)
            inv_node.clear_block_announcements()
            test_node.clear_block_announcements()
    return block_time


def _part_2(net: _Network, test_node: _Conn, inv_node: _Conn, block_time: int) -> int:
    """Core's own part 2: headers announcements once `sendheaders` is sent.

    :return: Core's own `block_time` at the part's end.
    """
    node0 = net.nodes[0]
    test_node.send(SendHeaders())
    prev_tip = _best(node0)
    test_node.send_get_headers(locator=[prev_tip], hashstop=_NO_STOP)
    test_node.sync_with_ping()

    # the headers synced, a new block is announced by its header
    tip = net.mine_blocks(1)
    expected_hash = tip
    inv_node.check_last_inv_announcement(inv=[tip])
    test_node.check_last_headers_announcement(headers=[tip])

    height = _count(node0) + 1
    block_time += 10
    for i in range(10):
        # the peer mines `i + 1` blocks and announces them by an `inv` of
        # the tip, then by their headers; the node's next block is
        # announced by its header either way, though the peer never asks
        # for it
        for j in range(2):
            blocks = []
            for _ in range(i + 1):
                blocks.append(_block(tip, height, block_time))
                tip = blocks[-1].header.hash
                block_time += 1
                height += 1
            if j == 0:
                test_node.send_block_inv(tip)
                if i == 0:
                    test_node.wait_for_getheaders(expected_hash)
                else:
                    test_node.sync_with_ping()
                    assert not test_node.has("getheaders")
                test_node.send_header_for_blocks(blocks)
                # a duplicate `inv` causes no duplicate `getdata`, nor
                # a duplicate headers announcement
                for block in blocks:
                    inv_node.send_block_inv(block.header.hash)
                test_node.wait_for_getdata([block.header.hash for block in blocks])
            else:
                test_node.send_header_for_blocks(blocks)
                test_node.wait_for_getdata([block.header.hash for block in blocks])
                # a duplicate `headers` causes no duplicate `getdata`,
                # checked below
                inv_node.send_header_for_blocks(blocks)
            inv_node.sync_with_ping()
            for block in blocks:
                test_node.send_block(block)
            test_node.sync_with_ping()
            inv_node.sync_with_ping()
            # not announced to the peer that announced them itself
            assert not inv_node.has("inv")
            assert not inv_node.has("headers")
            tip = net.mine_blocks(1)
            inv_node.check_last_inv_announcement(inv=[tip])
            test_node.check_last_headers_announcement(headers=[tip])
            height += 1
            block_time += 1
    return block_time


def _part_3(net: _Network, test_node: _Conn, inv_node: _Conn) -> None:
    """Core's own part 3: `inv`s after a long reorg, until the peer syncs."""
    node0 = net.nodes[0]
    for j in range(2):
        # a reorg short enough to be announced by its headers
        new_block_hashes = net.mine_reorg(length=7)
        tip = new_block_hashes[-1]
        inv_node.check_last_inv_announcement(inv=[tip])
        test_node.check_last_headers_announcement(headers=new_block_hashes)

        # one too long, announced by an `inv` of its tip
        new_block_hashes = net.mine_reorg(length=8)
        tip = new_block_hashes[-1]
        inv_node.check_last_inv_announcement(inv=[tip])
        test_node.check_last_inv_announcement(inv=[tip])

        previous = _getblock(node0, new_block_hashes[0])["previousblockhash"]
        fork_point = bytes.fromhex(str(previous))

        # `getblocks`, then `getdata`
        test_node.send_getblocks(locator=[fork_point])
        test_node.check_last_inv_announcement(inv=new_block_hashes)
        test_node.send_get_data(new_block_hashes)
        test_node.wait_for_block(new_block_hashes[-1])

        for i in range(3):
            # another block, still announced by an `inv`
            tip = net.mine_blocks(1)
            inv_node.check_last_inv_announcement(inv=[tip])
            test_node.check_last_inv_announcement(inv=[tip])
            if i == 0:
                # a `getdata` does not bring headers announcements back
                test_node.send_get_data([tip])
                test_node.wait_for_block(tip)
            elif i == 1:
                # nor does a `getheaders` whose best header is too old
                test_node.send_get_headers(
                    locator=[fork_point], hashstop=new_block_hashes[1]
                )
                test_node.send_get_data([tip])
                test_node.wait_for_block(tip)
            else:
                # a `getheaders` for the tip does, and so does an `inv`
                test_node.send_get_data([tip])
                test_node.wait_for_block(tip)
                if j == 0:
                    test_node.send_get_headers(locator=[tip], hashstop=_NO_STOP)
                else:
                    test_node.send_block_inv(tip)
                test_node.sync_with_ping()
        # the next block is announced by its header
        tip = net.mine_blocks(1)
        inv_node.check_last_inv_announcement(inv=[tip])
        test_node.check_last_headers_announcement(headers=[tip])


def _part_4(net: _Network, test_node: _Conn, inv_node: _Conn) -> tuple[bytes, int, int]:
    """Core's own part 4: direct fetch.

    :return: Core's own `tip`, `height` and `block_time` at the part's end.
    """
    node0 = net.nodes[0]
    tip = net.mine_blocks(1)
    height = _count(node0) + 1
    block_time = _block_time(node0) + 1

    # blocks already stored, then their headers: not fetched
    blocks = []
    for _ in range(2):
        blocks.append(_block(tip, height, block_time))
        tip = blocks[-1].header.hash
        block_time += 1
        height += 1
        inv_node.send_block(blocks[-1])
    inv_node.sync_with_ping()
    test_node.clear("getdata")
    test_node.send_header_for_blocks(blocks)
    test_node.sync_with_ping()
    assert not test_node.has("getdata")

    # headers of new blocks: fetched at once
    blocks = []
    for _ in range(3):
        blocks.append(_block(tip, height, block_time))
        tip = blocks[-1].header.hash
        block_time += 1
        height += 1
    test_node.send_header_for_blocks(blocks)
    test_node.sync_with_ping()
    test_node.wait_for_getdata(
        [block.header.hash for block in blocks], timeout=_DIRECT_FETCH_RESPONSE_TIME
    )
    for block in blocks:
        test_node.send_block(block)
    test_node.sync_with_ping()

    # a fork off the last two blocks, built in advance
    tip = blocks[0].header.hash
    height -= 2
    blocks = []
    for _ in range(20):
        blocks.append(_block(tip, height, block_time))
        tip = blocks[-1].header.hash
        block_time += 1
        height += 1

    # one header on the fork, less work than the tip: not fetched
    test_node.clear("getdata")
    test_node.send_header_for_blocks(blocks[:1])
    test_node.sync_with_ping()
    assert not test_node.has("getdata")

    # one more, as much work as the tip: both fetched
    test_node.send_header_for_blocks(blocks[1:2])
    test_node.sync_with_ping()
    test_node.wait_for_getdata(
        [block.header.hash for block in blocks[:2]],
        timeout=_DIRECT_FETCH_RESPONSE_TIME,
    )

    # more: fetched up to `MAX_BLOCKS_IN_TRANSIT_PER_PEER` in flight
    test_node.send_header_for_blocks(blocks[2:18])
    test_node.sync_with_ping()
    test_node.wait_for_getdata(
        [block.header.hash for block in blocks[2:16]],
        timeout=_DIRECT_FETCH_RESPONSE_TIME,
    )

    # one more: nothing
    test_node.clear("getdata")
    test_node.send_header_for_blocks(blocks[18:19])
    test_node.sync_with_ping()
    assert not test_node.has("getdata")

    # every block announced, delivered
    for block in blocks:
        test_node.send_block(block)
    return tip, height, block_time


def _part_5(
    net: _Network, test_node: _Conn, inv_node: _Conn, state: tuple[bytes, int, int]
) -> None:
    """Core's own part 5: headers that do not connect.

    :param state: Core's own `tip`, `height` and `block_time`, from part 4.
    """
    node0 = net.nodes[0]
    tip, height, block_time = state

    # a header that does not connect does not stop the chain's sync
    expected_hash = tip
    for _ in range(_NUM_HEADERS):
        test_node.clear("getdata")
        blocks = []
        for _ in range(2):
            blocks.append(_block(tip, height, block_time))
            tip = blocks[-1].header.hash
            block_time += 1
            height += 1
        # the second block's header alone does not connect
        test_node.send_header_for_blocks([blocks[1]])
        test_node.wait_for_getheaders(expected_hash)
        test_node.send_header_for_blocks(blocks)
        test_node.wait_for_getdata([block.header.hash for block in blocks])
        for block in blocks:
            test_node.send_block(block)
        test_node.sync_with_ping()
        assert _best(node0) == blocks[1].header.hash
        expected_hash = blocks[1].header.hash

    # headers that never connect are answered each time, with no end
    blocks = []
    for _ in range(_NUM_HEADERS + 1):
        blocks.append(_block(tip, height, block_time))
        tip = blocks[-1].header.hash
        block_time += 1
        height += 1
    for i in range(1, _NUM_HEADERS):
        test_node.clear("getheaders")
        # an empty `headers` answers the last `getheaders`, so that the
        # next header is an announcement rather than an answer
        test_node.send_header_for_blocks([])
        test_node.send_header_for_blocks([blocks[i]])
        test_node.wait_for_getheaders(expected_hash)

    # Core's own part 5c: with no empty `headers` before it, the header is
    # no answer to the `getheaders` in flight, which holds off the next
    test_node.clear("getheaders")
    test_node.send_header_for_blocks([blocks[_NUM_HEADERS]])
    test_node.sync_with_ping()
    assert not test_node.has("getheaders")
    # once `HEADERS_RESPONSE_TIME` has passed, a `getheaders` goes out again
    node0.set_mock_time(int(time.time()) + _HEADERS_RESPONSE_TIME + 1)
    test_node.send_header_for_blocks([blocks[_NUM_HEADERS]])
    test_node.wait_for_getheaders(expected_hash)
    node0.set_mock_time(0)

    # the peer reading `inv`s was never asked for a block
    inv_node.sync_with_ping()
    assert not inv_node.has("getdata")


def blocks_are_announced_by_headers_after_sendheaders(
    cluster: Callable[[int], Sequence[_Node]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own `setup_network` and `run_test`, in Core's own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    nodes = cluster(2)
    node0, node1 = nodes
    for node in nodes:
        require(Capability.GENERATE, node.capabilities, skip_counts)
    require(Capability.CONNECT, node1.capabilities, skip_counts)
    require(Capability.INVALIDATE_BLOCK, node1.capabilities, skip_counts)
    require(Capability.CLOCK, node0.capabilities, skip_counts)

    # Core's own `setup_network`: the second node dials the first
    connect_nodes(node1, node0)
    sync_all(nodes, timeout=_SYNC_TIMEOUT)
    net = _Network(nodes)

    with ExitStack() as stack:
        inv_node = _connect(stack, node0)
        # no `NODE_NETWORK`, so that no block is fetched from this peer
        # other than by direct fetch
        test_node = _connect(stack, node0, ServiceFlags.NODE_WITNESS)
        net.p2ps += [inv_node, test_node]

        _test_null_locators(net, test_node, inv_node)
        block_time = _part_1(net, test_node, inv_node)
        _part_2(net, test_node, inv_node, block_time)
        _part_3(net, test_node, inv_node)
        _part_5(net, test_node, inv_node, _part_4(net, test_node, inv_node))
