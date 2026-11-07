# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_invalid_messages`, its four `addrv2` checks, over each node.

Read from Core's `test/functional/p2p_invalid_messages.py`
(`3fd68a95e68b`, 2026-04-07)'s own `test_addrv2_empty`,
`test_addrv2_no_addresses`, `test_addrv2_too_long_address` and
`test_addrv2_unrecognized_network`, each through the shared
`test_addrv2`: an `addrv2` with a hand-built payload, its own
`serialize` overridden so the raw octets travel unchecked by either
side's own codec, over a `SenderOfAddrV2` -- Core's own peer that
waits for the node's `sendaddrv2` before sending anything, so a node not
yet announcing BIP155 support is never bombarded with a message it may
not recognise. This repository's own `Peer.handshake` already negotiates
`WTXID_RELAY_VERSION` (`70016`), the same floor `net_processing.cpp`
gates `SENDADDRV2` on (`Signal ADDRv2 support (BIP155)`), so the node has
already sent it by the time `handshake` returns, and no explicit wait is
needed to reach the same guarantee `SenderOfAddrV2` asks for by name.

None of Core's own four checks disconnects the peer -- an addrv2 that
fails to deserialize throws out of `ProcessMessage`, and
`PeerManagerImpl::ProcessMessages`'s own `catch` (`src/net_processing.cpp`)
logs the exception and moves on, the same drop-but-keep outcome
`p2p_invalid_messages_dropped_test.py`'s own docstring argues from
`CNode::ReceiveMsgBytes`'s comment (`src/net.cpp`) for a message the
transport rejects -- so each is a wire-and-log pair of the same shape that
module already uses: a `ping` round-trip confirms the connection is still
there, and `Capability.DEBUG_LOG` gates the log half.

That round-trip also proves the `addrv2` before it was handled, on both
nodes: each processes a peer's messages in the order received, btclib-node
since [ISS btclib-node#1410](https://github.com/btclib-org/btclib-node/issues/1410),
a build before it answering a `ping` ahead of the messages queued before it.

`test_addrv2_unrecognized_network`'s two log lines past the first,
`9.9.9.9:8333` and `Added 1 addresses`, are `LogDebug(BCLog::ADDRMAN,
...)`'s (`src/addrman.cpp`), which `BitcoindAdapter._command`'s own
`-debug=addrman` enables. Its node is started for it, with two settings
Core's own run of this file has and `bitcoind_adapter` does not.
`-whitelist=addr@127.0.0.1` is `InvalidMessagesTest.set_test_params`'s
own: without `NetPermissionFlags::Addr`, `net_processing.cpp`'s own
address-rate limiter processes one of the two entries and defers the
other, and `std::shuffle` picks which, so `9.9.9.9` is added on some
runs and not on others. `-connect=0` is what Core's own `write_config`
(`test_framework/util.py`) gives every node, and what keeps
`CConnman::Start` (`src/net.cpp`) from starting `ThreadOpenConnections`:
measured against the release `node-integration.yml` pins without it,
once `9.9.9.9` is in the address manager that thread selects it over and
over, each selection one more `addrman` line in the log. The wire half
starts one of these nodes too rather than using `bitcoind_adapter`,
which a session shares: left holding the address, that node would write
those lines for the rest of the session.

btclib-node is started the same way, with `-connect=0`, so
`P2pManager._maybe_dial_more_peers` (`p2p/manager.py`) returns before
drawing from the address table the message has just filled with
`9.9.9.9:8333`, where `btclib_node_adapter`, which a session shares, would
draw it and dial it once holding fewer connections than it wants -- read
from that code rather than observed, a dial logging nothing until it
connects. `-listen=1` beside it keeps the listener `Peer` reaches, which
`-connect` otherwise turns off, as it does in Core.

btcd is started with none of these options: it reads neither as Core
does, and its regtest parameters list no DNS seed (`chaincfg/params.go`).

Each `p2p_invalid_messages_addrv2_<node>_test.py` module beside this one
runs every body, `tests/integration/conftest.py`'s own module docstring
having how.
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import Message

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.btcd import BtcdAdapter
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import free_ports
from btclib_functional_tests.peer import Peer
from tests.integration.p2p_invalid_messages_dropped_test import debug_log, sync

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = [
    "addrv2_empty_is_logged",
    "addrv2_empty_keeps_the_connection",
    "addrv2_no_addresses_is_logged",
    "addrv2_no_addresses_keeps_the_connection",
    "addrv2_too_long_address_is_logged",
    "addrv2_too_long_address_keeps_the_connection",
    "addrv2_unrecognized_network_is_logged",
    "addrv2_unrecognized_network_keeps_the_connection",
]

_MAGIC = magic_from_chain("regtest")


def _addrv2_message(payload: bytes) -> bytes:
    """Return an `addrv2` message carrying `payload` unchecked."""
    message = Message(_MAGIC, "addrv2", payload, check_validity=False)
    return message.serialize(check_validity=False)


def addrv2_empty_keeps_the_connection(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: an empty `addrv2` is dropped, not disconnected.

    :param adapter: a node's own adapter fixture.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send_raw(_addrv2_message(b""))
        sync(peer)


def addrv2_empty_is_logged(adapter: NodeAdapter, skip_counts: SkipCounts) -> None:
    """Check the log half: bitcoind's own wording for what it silently dropped.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = debug_log(adapter, skip_counts)
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path,
            ["received: addrv2 (0 bytes)", "ProcessMessages(addrv2, 0 bytes)"],
        ),
    ):
        peer.handshake()
        peer.send_raw(_addrv2_message(b""))
        sync(peer)


def addrv2_no_addresses_keeps_the_connection(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: a zero-entry `addrv2` needs no rewriting either.

    :param adapter: a node's own adapter fixture.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send_raw(_addrv2_message(bytes.fromhex("00")))
        sync(peer)


def addrv2_no_addresses_is_logged(
    adapter: NodeAdapter, skip_counts: SkipCounts
) -> None:
    """Check the log half: bitcoind's own count of what it read and kept.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = debug_log(adapter, skip_counts)
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path,
            [
                "received: addrv2 (1 bytes)",
                "Received addr: 0 addresses (0 processed, 0 rate-limited)",
            ],
        ),
    ):
        peer.handshake()
        peer.send_raw(_addrv2_message(bytes.fromhex("00")))
        sync(peer)


# Core's own `test_addrv2_too_long_address`: one entry, a IPv4 network
# id claiming an address of 513 octets -- one more than `MAX_ADDRV2_SIZE`
# (`btclib.p2p.limits`, BIP155's own bound).
_TOO_LONG_ADDRESS = bytes.fromhex(
    "01"  # one entry
    "61bc6649"  # time
    "00"  # service flags, COMPACTSIZE(NODE_NONE)
    "01"  # network type (IPv4)
    "fd0102"  # address length, COMPACTSIZE(513)
    + "ab" * 513  # address
    + "208d"  # port
)


def addrv2_too_long_address_keeps_the_connection(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: an over-bound address is dropped, not disconnected.

    :param adapter: a node's own adapter fixture.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send_raw(_addrv2_message(_TOO_LONG_ADDRESS))
        sync(peer)


def addrv2_too_long_address_is_logged(
    adapter: NodeAdapter, skip_counts: SkipCounts
) -> None:
    """Check the log half: bitcoind's own wording for what it silently dropped.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    log_path = debug_log(adapter, skip_counts)
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path,
            [
                "received: addrv2 (525 bytes)",
                "ProcessMessages(addrv2, 525 bytes)",
                "Address too long: 513 > 512",
            ],
        ),
    ):
        peer.handshake()
        peer.send_raw(_addrv2_message(_TOO_LONG_ADDRESS))
        sync(peer)


def _addr_node(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    datadir: Path,
) -> NodeAdapter:
    """Return a node never dialling, not yet started, on ports of its own.

    The module docstring is why each of the flags is here: bitcoind's
    exempts it from address-rate limiting too, btclib-node's keeps its
    listener.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param datadir: the node's own data directory.
    """
    rpc_port, p2p_port = free_ports(2)
    extra_args: tuple[str, ...]
    if issubclass(cls, BitcoindAdapter):
        extra_args = ("-whitelist=addr@127.0.0.1", "-connect=0")
    elif issubclass(cls, BtcdAdapter):
        extra_args = ()
    else:
        extra_args = ("-connect=0", "-listen=1")
    return make_adapter(
        cls, executable, datadir, rpc_port, p2p_port, extra_args=extra_args
    )


@contextmanager
def _started(
    node: NodeAdapter,
) -> Iterator[NodeAdapter]:
    """Yield `node`, started, and stop it after."""
    node.start()
    try:
        yield node
    finally:
        node.stop()


def _unrecognized_network() -> bytes:
    """Return Core's own `test_addrv2_unrecognized_network` payload.

    Two entries stamped with the current time: an unrecognized network id,
    to be ignored without impeding the next entry, and `9.9.9.9:8333`,
    to be added.
    """
    now = int(time.time()).to_bytes(4, "little").hex()
    return bytes.fromhex(
        "02"  # two entries
        + now  # time
        + "01"  # service flags, COMPACTSIZE(NODE_NETWORK)
        + "99"  # network type (unrecognized)
        + "02"  # address length, COMPACTSIZE(2)
        + "ab" * 2  # address
        + "208d"  # port
        + now  # time
        + "01"  # service flags, COMPACTSIZE(NODE_NETWORK)
        + "01"  # network type (IPv4)
        + "04"  # address length, COMPACTSIZE(4)
        + "09" * 4  # address
        + "208d"  # port
    )


def addrv2_unrecognized_network_keeps_the_connection(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
) -> None:
    """Check the wire half: an entry of an unknown network is ignored.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    """
    with (
        _started(_addr_node(make_adapter, cls, executable, tmp_path)) as node,
        Peer(node.p2p_address, _MAGIC) as peer,
    ):
        peer.handshake()
        peer.send_raw(_addrv2_message(_unrecognized_network()))
        sync(peer)


def addrv2_unrecognized_network_is_logged(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check the log half: the entry after the unrecognized one is still added.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    node = _addr_node(make_adapter, cls, executable, tmp_path)
    log_path = debug_log(node, skip_counts)
    with (
        _started(node),
        Peer(node.p2p_address, _MAGIC) as peer,
        assert_debug_log(
            log_path,
            ["received: addrv2 (25 bytes)", "9.9.9.9:8333", "Added 1 addresses"],
        ),
    ):
        peer.handshake()
        peer.send_raw(_addrv2_message(_unrecognized_network()))
        sync(peer)
