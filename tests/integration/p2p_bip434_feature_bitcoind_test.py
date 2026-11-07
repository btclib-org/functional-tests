# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_bip434_feature`, rewritten on tf2's own harness: bitcoind.

Read from Core's `test/functional/p2p_bip434_feature.py` (`da74ff9ca49e`,
2026-06-04), narrowed twice over from Core's own file. First, to what a
build lacking BIP434 support disconnects for anyway rather than to
`FEATURE`'s own accepted shapes: `net_processing.cpp`'s own handling of
`NetMsgType::FEATURE` landed in
`6a129983c9bf8efa1081f9a8b462c3635d1cfb39` ("BIP434: FEATURE message
support"), the same commit that bumped `node/protocol_version.h`'s own
`PROTOCOL_VERSION` from `70016` to `70017` -- so a build below that
version does not merely negotiate `FEATURE` differently, it has no branch
for the `"feature"` command at all, and the length-boundary and
acceptance checks Core's own file also carries
(`test_feature_id_length_boundaries`, `test_many_features_in_handshake`,
and the rest) read the log of a node Core's file starts with
`-peertimeout` above its default, so they need more than one of step 5's
mechanisms (issue #14) and are not this row's; this module passes no
`-peertimeout`. What is kept is the two disconnects observable on the
wire alone, with no log needed to confirm either: a `FEATURE` sent after
this peer's own `verack` completes, and one sent by a peer that never got
as far as offering the version `FEATURE` needs at all.

Second, to a fact read from the running build rather than assumed for
the whole class ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35),
`capability.py`'s own module docstring): every test below reads
`getnetworkinfo`'s own `protocolversion` and asserts whichever of the
two shapes the running build actually carries, `feature_torcontrol`'s
own style (`tests/integration/feature_torcontrol_bitcoind_test.py`)
rather than a skip -- a build lacking `FEATURE` support does not merely
answer the wire differently, it silently ignores the message and the
connection survives, which `_survives` below confirms the same way
`p2p_invalid_messages_dropped_test.py`'s own `sync` does, over
a `ping`/`pong` round trip rather than over a timeout with nothing to
observe. A `pytest.skip` was tried first and reverted: bitcoind is rule
3's own oracle, and
`btclib-org/.github`'s `reusable-integration-bitcoind.yml` fails the
required job on any skip whose reason does not start with its own
`skip-reason-prefix` -- `tests/integration/conftest.py`'s `NO_TARGET`,
which `node-integration.yml`'s `bitcoind` job passes -- so a
build-dependent skip on this row would have failed every pull request
touching this file, discovered only by dispatching
`node-integration.yml` rather than by any local gate.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import Feature, Ping, Pong, ServiceFlags, Verack, Version, WtxidRelay

from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from btclib_functional_tests.bitcoind import BitcoindAdapter

pytestmark = pytest.mark.integration

_MAGIC = magic_from_chain("regtest")
_SERVICES = ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS
_USER_AGENT = b"/btclib-functional-tests:0/"

# node/protocol_version.h, read at Core's `master`: the protocol version
# BIP434's FEATURE message needs, and one no release before `v32.0`
# reaches (module docstring above).
_FEATURE_VERSION = 70017


def _protocol_version(bitcoind_adapter: BitcoindAdapter) -> int:
    """Return the running build's own p2p protocol version.

    `getnetworkinfo`'s own `protocolversion`, an RPC call rather than a
    fresh p2p connection: every test below needs this before it decides
    which of the two shapes to assert, and
    `test_advertises_its_own_protocol_version` is what checks this RPC
    fact agrees with the wire's own.
    """
    info = bitcoind_adapter.rpc.call("getnetworkinfo")
    return int(info["protocolversion"])


def _survives(peer: Peer) -> None:
    """Round-trip a `ping`, confirming `peer`'s own connection is still up.

    `p2p_invalid_messages_dropped_test.py`'s own `sync`, the
    established idiom here for "this connection is still usable" -- a
    build with no `"feature"` branch at all treats the message the same
    way it treats any other one it does not recognise, silently.
    """
    nonce = secrets.randbelow(2**64)
    peer.send(Ping(nonce))
    peer.wait_for("pong", predicate=lambda m: Pong.parse(m.payload).nonce == nonce)


def test_advertises_its_own_protocol_version(bitcoind_adapter: BitcoindAdapter) -> None:
    """The `version` message's own field names what `getnetworkinfo` reports.

    Runs on every build: this is the narrower, build-independent claim
    the two tests below rest on, rather than Core's own
    `test_advertised_version`, which only holds once every pinned
    release speaks `_FEATURE_VERSION`.
    """
    with Peer(bitcoind_adapter.p2p_address, _MAGIC) as peer:
        version_message = peer.handshake()
    assert version_message.version == _protocol_version(bitcoind_adapter)


def test_feature_after_verack(bitcoind_adapter: BitcoindAdapter) -> None:
    """A well-formed `FEATURE`, sent once the handshake is done.

    `net_processing.cpp`'s own `fSuccessfullyConnected` check is the
    first one `NetMsgType::FEATURE`'s handler makes, ahead of any check
    on the negotiated version, so this disconnects on any build that
    parses the command at all, and is silently ignored -- the connection
    surviving a `ping`/`pong` round trip -- on one that does not.
    """
    with Peer(bitcoind_adapter.p2p_address, _MAGIC) as peer:
        peer.handshake()
        peer.send(Feature(b"abcd", b""))
        if _protocol_version(bitcoind_adapter) >= _FEATURE_VERSION:
            peer.wait_for_disconnect()
        else:
            _survives(peer)


def test_feature_before_verack_from_a_pre_feature_peer(
    bitcoind_adapter: BitcoindAdapter,
) -> None:
    """A peer offering below `_FEATURE_VERSION`, sending `FEATURE` early.

    `Version`'s own default (`btclib.p2p`) is below `_FEATURE_VERSION`,
    so this peer's own handshake already puts it where
    `net_processing.cpp`'s own `GetCommonVersion() < FEATURE_VERSION`
    check fires on a build that has it -- reached only ahead of `verack`,
    which this peer completes only where the node is not expected to
    have disconnected it first.
    """
    with Peer(bitcoind_adapter.p2p_address, _MAGIC) as peer:
        peer.send(Version(services=_SERVICES, user_agent=_USER_AGENT, nonce=1))
        peer.wait_for("version")
        peer.send(Feature(b"abcd", b""))
        if _protocol_version(bitcoind_adapter) >= _FEATURE_VERSION:
            peer.wait_for_disconnect()
        else:
            peer.send(WtxidRelay())
            peer.send(Verack())
            peer.wait_for("verack")
