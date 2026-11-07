# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_leak`: an obsolete version and a feature-negotiation boundary.

The obsolete version has a wire body and a log body, the boundary a wire
body, all over each node.

Read from Core's `test/functional/p2p_leak.py` (`36775471f81a`,
2026-09-09)'s own closing check, "old peers are disconnected", and its
feature-negotiation version boundary, rather than ported whole: the rest
of `P2PLeakTest.run_test` asks what a node sends *before* a handshake
completes and what a `version` message itself carries, which are fields
and orderings each implementation chooses for itself.

The boundary is the one part of that check ported: it asks only which
commands reach a peer. BIP339 requires `wtxidrelay` for a peer at 70016 or
higher, and BIP434 forbids a `feature` to a peer below 70017. No BIP fixes
the rest: Core withholds `wtxidrelay` below 70016, and sends `sendaddrv2`
only from 70016, as a courtesy to nodes that reject messages they do not
know (BIP155 sets no minimum version; `src/net_processing.cpp`,
bitcoin/bitcoin@f0de87f040). The body holds a node to Core's behaviour
there.

It is Core's two peers that send a `version` and no `verack`, at 70015 and
at exactly 70016
(BIP339's `WTXID_RELAY_VERSION`): the node sends `wtxidrelay` and
`sendaddrv2` to the second and to the first neither, and sends neither of
them a `feature`. Where Core waits for each peer's own timeout and reads
what it received, `feature_negotiation_starts_at_the_wtxid_version`
reads up to the node's `verack`, which comes after the messages
negotiated.

Core's own check wraps the send in `assert_debug_log(["using obsolete
version 31799, disconnecting peer=5"])` and then calls
`wait_for_disconnect` -- the disconnect itself is `net_processing.cpp`'s
own `nVersion < MIN_PEER_PROTO_VERSION` (`node/protocol_version.h`,
`31800`), observable on the wire with no log at all, so
`obsolete_version_disconnects_the_peer` below asks for nothing.
`obsolete_version_is_logged` is the log half, Core's own wording for
*why* -- `peer using obsolete version` (`net_processing.cpp`), with
`peer=5` left out of the match, that number being Core's own connection
count in its longer test rather than a fact of the obsolete-version
check itself.

Each `p2p_leak_<node>_test.py` module beside this one runs each,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import ServiceFlags, Version

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "feature_negotiation_starts_at_the_wtxid_version",
    "obsolete_version_disconnects_the_peer",
    "obsolete_version_is_logged",
]

_MAGIC = magic_from_chain("regtest")
# Core's own `create_old_version`, and the exact version its test picks:
# one below `MIN_PEER_PROTO_VERSION` (`node/protocol_version.h`, 31800).
_OBSOLETE_VERSION = 31799
# BIP339's `WTXID_RELAY_VERSION`, where feature negotiation starts, and
# the version below it
_WTXID_RELAY_VERSION = 70016
_PRE_WTXID_RELAY_VERSION = _WTXID_RELAY_VERSION - 1


def _send_obsolete_version(peer: Peer) -> None:
    peer.send(
        Version(
            version=_OBSOLETE_VERSION,
            services=ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS,
            nonce=1,
        )
    )


def obsolete_version_disconnects_the_peer(
    adapter: NodeAdapter,
) -> None:
    """Check the wire half: a version below the minimum gets the peer dropped.

    :param adapter: a node's own adapter fixture.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        _send_obsolete_version(peer)
        peer.wait_for_disconnect()


def obsolete_version_is_logged(adapter: NodeAdapter, skip_counts: SkipCounts) -> None:
    """Check the log half: bitcoind's own wording for why it disconnected.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    :raises TypeError: `adapter` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, adapter.capabilities, skip_counts)
    if not isinstance(adapter, BitcoindAdapter):
        err_msg = f"{type(adapter).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    with (
        Peer(adapter.p2p_address, _MAGIC) as peer,
        assert_debug_log(adapter.debug_log_path, ["peer using obsolete version"]),
    ):
        _send_obsolete_version(peer)
        peer.wait_for_disconnect()


def _commands_before_verack(adapter: NodeAdapter, version: int) -> list[str]:
    """Send a `version` of `version` and no `verack`, and read up to the node's.

    :returns: every command the node sent, `verack` last.
    """
    with Peer(adapter.p2p_address, _MAGIC) as peer:
        peer.send(
            Version(
                version=version,
                services=ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS,
                nonce=1,
            )
        )
        commands: list[str] = []
        while not commands or commands[-1] != "verack":
            commands.append(peer.receive().command)
        return commands


def feature_negotiation_starts_at_the_wtxid_version(
    adapter: NodeAdapter,
) -> None:
    """Check `wtxidrelay` and `sendaddrv2` go to a peer at 70016, not at 70015.

    :param adapter: a node's own adapter fixture.
    """
    below = _commands_before_verack(adapter, _PRE_WTXID_RELAY_VERSION)
    assert "wtxidrelay" not in below
    assert "sendaddrv2" not in below
    assert "feature" not in below

    at = _commands_before_verack(adapter, _WTXID_RELAY_VERSION)
    assert "wtxidrelay" in at
    assert "sendaddrv2" in at
    assert "feature" not in at
