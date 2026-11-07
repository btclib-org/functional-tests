# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_invalid_messages`, its `Misbehaving` checks, over each node.

Read from Core's `test/functional/p2p_invalid_messages.py`
(`3fd68a95e68b`, 2026-04-07)'s own `test_oversized_inv_msg`,
`test_oversized_getdata_msg`, `test_oversized_headers_msg` (each through
the shared `test_oversized_msg`), `test_invalid_pow_headers_msg` and
`test_noncontinuous_headers_msg`, none ported by
`p2p_invalid_messages_test.py`: that module's own docstring narrows to
`test_magic_bytes` and `test_size`, and Core's own
`InvalidMessagesTest.run_test` runs every one of these on the same node,
started once with `self.extra_args = [["-whitelist=addr@127.0.0.1"]]`.
That permission (`src/net_permissions.cpp`'s `TryParsePermissionFlags`,
bitcoin/bitcoin@f0de87f040: the string before `@` is parsed as a
permission list, and `addr` is one) grants `NetPermissionFlags::Addr`
alone, not `NoBan` -- so it does not exempt this connection from the
discourage-and-disconnect `net_processing.cpp`'s own `Misbehaving`
schedules (`PeerManagerImpl::MaybeDiscourageAndDisconnect`, `if
(pnode.HasPermission(NetPermissionFlags::NoBan)) { ... never disconnect
... }`), which is what the checks below are about; dropping it is the
same narrowing `test_magic_bytes` already made.

Each of Core's own checks calls `assert_debug_log(['Misbehaving', ...])`
around an oversized, PoW-invalid or non-continuous message and then waits for a
disconnect: two facts of one action, so each gets a wire half and a log
half of its own, the log family's own rule (issue #5). The oversized and
PoW wire halves need nothing this step does not already give every node
under test, and the non-continuous one asks for `Capability.MINE`. The
log half is bitcoind's own wording for *why*, `Capability.DEBUG_LOG`
gating it as `p2p_invalid_messages_test.py`'s own two log halves already
do.

Core's own `nCount`/`vInv.size()` check runs before any of the
message's own bytes past the count are read
(`net_processing.cpp`'s own `HEADERS`, `INV` and `GETDATA` branches, each
`Misbehaving` and `return` before touching an element), so the entries
themselves carry no claim beyond their own count and their own valid
framing -- `Inventory()`'s defaults for `inv`/`getdata`, and one
genesis-derived `BlockHeader` repeated for `headers`.

`invalid_pow_header_*` drops Core's own preliminary "send a valid
header first" step: `PeerManagerImpl::CheckHeadersPoW`
(`net_processing.cpp`) runs unconditionally at the top of
`ProcessHeadersMessage`, before any chain-state read, so a single
invalid-PoW header extending genesis reaches the same `Misbehaving` call
with no prior exchange needed. The invalid header is genesis's own
`bits` -- regtest's own trivial target, `0x207fffff` -- with a nonce
searched until the header's own displayed hash starts with `f`, Core's
own test's criterion and, under `0x207fffff`'s target (whose own top
byte is `0x7f`), sufficient on its own: any hash Core's own
`CheckProofOfWork` would refuse for exceeding this target.

`noncontinuous_headers_*` mine the blocks Core's own check mines
(`Capability.MINE`), send their headers in order, and then send them
again with one header dropped. Core drops a random one past the first and
before the last; this drops the middle one. The wire half reads the
first send as the connection surviving a `sync_with_ping`, and the second
as a disconnect. The log half reads the first send as no `Misbehaving`
line, Core's own `unexpected_msgs`, and the second as Core's wording.

Each `p2p_invalid_messages_misbehaving_<node>_test.py` module beside
this one runs every body, `tests/integration/conftest.py`'s own module
docstring having how.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.block import genesis_block
from btclib.block.block_header import BlockHeader
from btclib.p2p import GetData, Headers, Inv, Inventory
from btclib.p2p.limits import MAX_HEADERS_RESULTS, MAX_INV_SZ

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "invalid_pow_header_disconnects_the_peer",
    "invalid_pow_header_is_logged",
    "noncontinuous_headers_disconnects_the_peer",
    "noncontinuous_headers_is_logged",
    "oversized_getdata_disconnects_the_peer",
    "oversized_getdata_is_logged",
    "oversized_headers_disconnects_the_peer",
    "oversized_headers_is_logged",
    "oversized_inv_disconnects_the_peer",
    "oversized_inv_is_logged",
]

_MAGIC = magic_from_chain("regtest")
_GENESIS_HEADER = genesis_block("regtest").header

# the blocks Core's own `test_noncontinuous_headers_msg` mines
_NONCONTINUOUS_BLOCKS = 10

_NONCONTINUOUS_LOG = ["Misbehaving", "non-continuous headers sequence"]


def _oversized_inv() -> Inv:
    """Return an `inv` one entry over `MAX_INV_SZ`, unchecked at build time."""
    return Inv([Inventory()] * (MAX_INV_SZ + 1), check_validity=False)


def _oversized_getdata() -> GetData:
    """Return a `getdata` one over `MAX_INV_SZ`, unchecked at build time."""
    return GetData([Inventory()] * (MAX_INV_SZ + 1), check_validity=False)


def _oversized_headers() -> Headers:
    """Return a `headers` one entry over `MAX_HEADERS_RESULTS`, unchecked."""
    return Headers([_GENESIS_HEADER] * (MAX_HEADERS_RESULTS + 1), check_validity=False)


def _invalid_pow_header() -> BlockHeader:
    """Return a header extending genesis whose own hash fails its target.

    Genesis's own `bits`, so the target is the one `CheckProofOfWork`
    holds this header to; the nonce is the first for which the header's
    own displayed hash starts with `f`, Core's own test's search and,
    under regtest's `0x207fffff` (target's own top byte `0x7f`),
    sufficient by itself: no hash starting `f` can be at or under it.
    Each call stamps its own time, so the octets differ from call to call.
    """
    nonce = 0
    while True:
        header = BlockHeader(
            1,
            _GENESIS_HEADER.hash,
            bytes(32),
            datetime.now(UTC),
            _GENESIS_HEADER.bits,
            nonce,
            check_validity=False,
        )
        if header.hash[0] >= 0xF0:
            return header
        nonce += 1


def _debug_log(adapter: NodeAdapter, skip_counts: SkipCounts) -> Path:
    """Return the log a log half reads, or skip where the node keeps none.

    :raises TypeError: `adapter` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, adapter.capabilities, skip_counts)
    if not isinstance(adapter, BitcoindAdapter):
        err_msg = f"{type(adapter).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return adapter.debug_log_path


def oversized_inv_disconnects_the_peer(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: an `inv` over `MAX_INV_SZ` gets the peer dropped.

    :param adapter: a node's own adapter fixture.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send(_oversized_inv(), check_validity=False)
        peer.wait_for_disconnect()


def oversized_inv_is_logged(adapter: NodeAdapter, skip_counts: SkipCounts) -> None:
    """Check the log half: bitcoind's own wording for why it disconnected.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = _debug_log(adapter, skip_counts)
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path, ["Misbehaving", f"inv message size = {MAX_INV_SZ + 1}"]
        ),
    ):
        peer.handshake()
        peer.send(_oversized_inv(), check_validity=False)
        peer.wait_for_disconnect()


def oversized_getdata_disconnects_the_peer(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: a `getdata` over `MAX_INV_SZ` drops the peer.

    :param adapter: a node's own adapter fixture.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send(_oversized_getdata(), check_validity=False)
        peer.wait_for_disconnect()


def oversized_getdata_is_logged(adapter: NodeAdapter, skip_counts: SkipCounts) -> None:
    """Check the log half: bitcoind's own wording for why it disconnected.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = _debug_log(adapter, skip_counts)
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path, ["Misbehaving", f"getdata message size = {MAX_INV_SZ + 1}"]
        ),
    ):
        peer.handshake()
        peer.send(_oversized_getdata(), check_validity=False)
        peer.wait_for_disconnect()


def oversized_headers_disconnects_the_peer(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: `headers` over `MAX_HEADERS_RESULTS` disconnects.

    :param adapter: a node's own adapter fixture.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send(_oversized_headers(), check_validity=False)
        peer.wait_for_disconnect()


def oversized_headers_is_logged(adapter: NodeAdapter, skip_counts: SkipCounts) -> None:
    """Check the log half: bitcoind's own wording for why it disconnected.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = _debug_log(adapter, skip_counts)
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path,
            ["Misbehaving", f"headers message size = {MAX_HEADERS_RESULTS + 1}"],
        ),
    ):
        peer.handshake()
        peer.send(_oversized_headers(), check_validity=False)
        peer.wait_for_disconnect()


def invalid_pow_header_disconnects_the_peer(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: a header failing its own target disconnects.

    :param adapter: a node's own adapter fixture.
    """
    header = _invalid_pow_header()
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send(Headers([header]))
        peer.wait_for_disconnect()


def invalid_pow_header_is_logged(adapter: NodeAdapter, skip_counts: SkipCounts) -> None:
    """Check the log half: bitcoind's own wording for why it disconnected.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = _debug_log(adapter, skip_counts)
    header = _invalid_pow_header()
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path, ["Misbehaving", "header with invalid proof of work"]
        ),
    ):
        peer.handshake()
        peer.send(Headers([header]))
        peer.wait_for_disconnect()


def _mined_headers(adapter: NodeAdapter, skip_counts: SkipCounts) -> list[BlockHeader]:
    """Mine Core's blocks, and return their headers, oldest first."""
    require(Capability.MINE, adapter.capabilities, skip_counts)
    headers = []
    for block_hash in adapter.mine(_NONCONTINUOUS_BLOCKS):
        raw = adapter.rpc.call("getblockheader", [block_hash, False])
        headers.append(BlockHeader.parse(bytes.fromhex(str(raw))))
    return headers


def _without_the_middle(headers: list[BlockHeader]) -> Headers:
    """Return `headers` less the middle one, a sequence with a gap."""
    middle = len(headers) // 2
    return Headers(headers[:middle] + headers[middle + 1 :])


def noncontinuous_headers_disconnects_the_peer(
    adapter: NodeAdapter, skip_counts: SkipCounts
) -> None:
    """Check the wire half: only a `headers` message with a gap drops the peer.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    headers = _mined_headers(adapter, skip_counts)
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send(Headers(headers))
        peer.sync_with_ping()
        peer.send(_without_the_middle(headers))
        peer.wait_for_disconnect()


def noncontinuous_headers_is_logged(
    adapter: NodeAdapter, skip_counts: SkipCounts
) -> None:
    """Check the log half: bitcoind's own wording, only for the gap.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = _debug_log(adapter, skip_counts)
    headers = _mined_headers(adapter, skip_counts)
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        with assert_debug_log(log_path, [], _NONCONTINUOUS_LOG):
            peer.send(Headers(headers))
            peer.sync_with_ping()
        with assert_debug_log(log_path, _NONCONTINUOUS_LOG):
            peer.send(_without_the_middle(headers))
            peer.wait_for_disconnect()
