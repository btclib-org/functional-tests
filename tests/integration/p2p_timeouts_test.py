# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_timeouts`, one body over each node.

Read from Core's `test/functional/p2p_timeouts.py` (`fa4cb96bdec2`,
2026-02-17), the option, clock and log families together
([ISS 14](https://github.com/btclib-org/functional-tests/issues/14)):
a node started with `-peertimeout=3` (`Capability.PEER_TIMEOUT`) keeps
three peers that never finish the handshake -- one sending `version`
and never `verack`, one sending only `ping`, one sending nothing -- for
two seconds of its own clock (`Capability.CLOCK`), and drops all three
two seconds later. `CConnman::InactivityCheck` (`src/net.cpp`) reads
`m_peer_connect_timeout` and the mockable clock, so the clock alone
decides when each check first reaches a peer.

Each fact of Core's own `run_test` is given the half it is observable
in, the log family's own rule (issue #5). The wire half reads whether
the node still holds each connection off `getpeerinfo`, and each drop
off `Peer.wait_for_disconnect`; Core reads its own side's
`is_connected` for the first and the same wait for the second. The log
half asserts Core's own lines around the same steps
(`Capability.DEBUG_LOG`): the connection each peer opens, the `ping`
each refused before the handshake, and the reason each drop gives. Its
peer ids are the node's first three, each test starting its own node.
Core's `-v2transport` branch of the expected lines is dropped: the node
runs with `TestNode`'s default, `-v2transport=0`, below.

The node is restarted with two settings Core's own harness gives every
node it starts and `BitcoindAdapter` does not. `-v2transport=0` is
`TestNode.__init__`'s own default: under bitcoind's default of `1`, a
connection whose first message is not `version` is taken for a BIP324
one, and the node answers the `ping` of the peer that never sent
`version` with a v2 handshake rather than the log line Core asserts,
measured against the release `node-integration.yml` pins. `-connect=0`
is `write_config`'s own (`test_framework/util.py`,
`disable_autoconnect`), leaving every connection the node holds one
the test opened.

Core's closing check, a start with `-peertimeout=0` refused with its own
`expected_msg`, is a third test, matched against the whole of the node's
stderr as `assert_start_raises_init_error` matches it by default.

Each `p2p_timeouts_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import contextlib
import re
import time
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import magic_from_chain
from btclib.p2p import Ping, Version

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import wait_until
from btclib_functional_tests.peer import Peer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from contextlib import AbstractContextManager

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_non_positive_peertimeout_is_refused",
    "handshake_timeouts_are_logged",
    "peers_that_never_finish_the_handshake_are_dropped",
]

# Core's own `extra_args`: three seconds to receive `version` and `verack`
_PEER_TIMEOUT = 3

# what Core's own harness gives every node it starts, this module's own
# docstring having why each matters here
_HARNESS_ARGS = ("-v2transport=0", "-connect=0")

# Core's own `expected_msg`, and `_wait_for_rpc`'s own wording (`node.py`)
# split into the exit code and the stderr it carries
_REFUSAL = "Error: peertimeout must be a positive integer."
_EARLY_EXIT = re.compile(
    r"node process exited with (-?\d+) before its RPC answered -- stderr: (.*)",
    re.DOTALL,
)


def _peer_count(node: NodeAdapter) -> int:
    """Return how many connections `getpeerinfo` says the node holds."""
    peers = node.rpc.call("getpeerinfo")
    assert isinstance(peers, list)
    return len(peers)


def _handshake_timeouts(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    *,
    logged: bool,
) -> None:
    """Run Core's own `run_test`, asserting its log lines where `logged`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    :param logged: whether to require `Capability.DEBUG_LOG` and assert
        Core's own lines around each step.
    :raises TypeError: `logged`, and the node declares
        `Capability.DEBUG_LOG` without being the adapter that names a
        `debug_log_path`.
    """
    (node,) = cluster(1)
    require(Capability.PEER_TIMEOUT, node.capabilities, skip_counts)
    require(Capability.CLOCK, node.capabilities, skip_counts)
    if logged:
        require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
        if not isinstance(node, BitcoindAdapter):
            err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
            raise TypeError(err_msg)
        log_path = node.debug_log_path

    def expect(lines: list[str]) -> AbstractContextManager[None]:
        """Return Core's `assert_debug_log` over `lines`, or no check."""
        if logged:
            return assert_debug_log(log_path, lines)
        return contextlib.nullcontext()

    node.restart([f"-peertimeout={_PEER_TIMEOUT}", *_HARNESS_ARGS])
    magic = magic_from_chain(node.chain)
    mock_time = int(time.time())

    def mock_forward(delta: int) -> None:
        nonlocal mock_time
        mock_time += delta
        node.set_mock_time(mock_time)

    mock_forward(0)
    with contextlib.ExitStack() as peers:
        # each connection is held by the node before the clock moves, the
        # way Core waits for "Added connection" before bumping it
        with expect(["Added connection peer=0"]):
            no_verack = peers.enter_context(Peer(node.p2p_address, magic))
            no_verack.send(Version(nonce=1))
            wait_until(lambda: _peer_count(node) == 1)
        with expect(["Added connection peer=1"]):
            no_version = peers.enter_context(Peer(node.p2p_address, magic))
            wait_until(lambda: _peer_count(node) == 2)
        with expect(["Added connection peer=2"]):
            no_send = peers.enter_context(Peer(node.p2p_address, magic))
            wait_until(lambda: _peer_count(node) == 3)

        # the node answers the version with its own and a verack, and never
        # sees one back
        no_verack.wait_for("verack")

        mock_forward(1)
        assert _peer_count(node) == 3

        with expect(['Unsupported message "ping" prior to verack from peer=0']):
            no_verack.send(Ping(0))
        with expect(
            ['non-version message before version handshake. Message "ping" from peer=1']
        ):
            no_version.send(Ping(0))

        mock_forward(1)
        assert "version" in no_verack.last_message
        assert _peer_count(node) == 3

        no_verack.send(Ping(0))
        no_version.send(Ping(0))

        with expect(
            [
                "version handshake timeout, disconnecting peer=0",
                (
                    f"socket no message in first {_PEER_TIMEOUT} seconds, "
                    "never sent to peer, disconnecting peer=1"
                ),
                (
                    f"socket no message in first {_PEER_TIMEOUT} seconds, "
                    "never received from peer, never sent to peer, "
                    "disconnecting peer=2"
                ),
            ]
        ):
            mock_forward(2)
            no_verack.wait_for_disconnect()
            no_version.wait_for_disconnect()
            no_send.wait_for_disconnect()


def peers_that_never_finish_the_handshake_are_dropped(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check the wire half: all three kept at two seconds, dropped at four.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _handshake_timeouts(cluster, skip_counts, logged=False)


def handshake_timeouts_are_logged(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check the log half: Core's own lines, around the same steps.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    _handshake_timeouts(cluster, skip_counts, logged=True)


def a_non_positive_peertimeout_is_refused(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check a start with `-peertimeout=0` fails, with Core's own wording.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.PEER_TIMEOUT, node.capabilities, skip_counts)
    node.stop()
    with pytest.raises(RuntimeError) as refused:
        node.restart(["-peertimeout=0"])
    early_exit = _EARLY_EXIT.fullmatch(str(refused.value))
    assert early_exit is not None
    assert int(early_exit[1]) != 0
    assert early_exit[2].strip() == _REFUSAL
