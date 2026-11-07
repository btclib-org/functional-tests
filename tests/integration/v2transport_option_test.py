# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `--v2transport`, restated through the adapter, over each node.

Issue [functional-tests#36](https://github.com/btclib-org/functional-tests/issues/36):
this suite has no central test-framework object for Core's own
`--v2transport`/`--v1transport` to set a default on, so the equivalent is
Core's own `-v2transport` flag, node by node, here through
`NodeAdapter.restart`'s own `extra_args` -- the same mechanism
`feature_uacomment_test.py` uses for `-uacomment`. `getpeerinfo`'s own
`transport_protocol_type` is what a real connection answers with, measured
live against the release `node-integration.yml` pins for both values of the
flag.

Each `v2transport_option_<node>_test.py` module beside this one runs each body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import connect_nodes

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "v2transport_0_connects_nodes_over_v1",
    "v2transport_1_connects_nodes_over_bip324",
]


def _only_peer(adapter: NodeAdapter) -> dict[str, object]:
    """Return the one peer's own `getpeerinfo` entry, or raise.

    :param adapter: a node with exactly one peer connected.
    :raises TypeError: `getpeerinfo` answered something other than a
        one-peer list of objects.
    """
    peers = adapter.rpc.call("getpeerinfo")
    if not isinstance(peers, list) or len(peers) != 1:
        err_msg = f"getpeerinfo answered {peers!r}, not a one-peer list"
        raise TypeError(err_msg)
    (peer,) = peers
    if not isinstance(peer, dict):
        err_msg = f"getpeerinfo's one peer was {peer!r}, not an object"
        raise TypeError(err_msg)
    return peer


def _transport_protocol(adapter: NodeAdapter) -> str:
    """Return the one peer's own `transport_protocol_type`, or raise.

    :param adapter: a node with exactly one peer connected.
    :raises TypeError: the field was not a string.
    """
    protocol = _only_peer(adapter)["transport_protocol_type"]
    if not isinstance(protocol, str):
        err_msg = f"transport_protocol_type was {protocol!r}, not a string"
        raise TypeError(err_msg)
    return protocol


def _session_id(adapter: NodeAdapter) -> str:
    """Return the one peer's own `session_id`, or raise.

    :param adapter: a node with exactly one peer connected.
    :raises TypeError: the field was not a string.
    """
    session_id = _only_peer(adapter)["session_id"]
    if not isinstance(session_id, str):
        err_msg = f"session_id was {session_id!r}, not a string"
        raise TypeError(err_msg)
    return session_id


def _pair(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    flag: str,
) -> tuple[NodeAdapter, NodeAdapter]:
    """Return two nodes, each restarted with `flag`.

    The first node's own `Capability.V2TRANSPORT` is asked before the
    second one starts, so a node not declaring it is skipped on one start.
    """
    (first,) = cluster(1)
    require(Capability.V2TRANSPORT, first.capabilities, skip_counts)
    first, second = cluster(1)
    first.restart([flag])
    second.restart([flag])
    return first, second


def v2transport_1_connects_nodes_over_bip324(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `-v2transport=1` on both sides: `v2`, and one `session_id`.

    BIP324 derives the session id from the handshake, so both ends read
    the same non-empty one.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    first, second = _pair(cluster, skip_counts, "-v2transport=1")
    connect_nodes(first, second, v2transport=True)
    assert _transport_protocol(first) == "v2"
    assert _transport_protocol(second) == "v2"
    assert _session_id(first)
    assert _session_id(first) == _session_id(second)


def v2transport_0_connects_nodes_over_v1(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `-v2transport=0` on both sides: `getpeerinfo` answers `v1`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    first, second = _pair(cluster, skip_counts, "-v2transport=0")
    connect_nodes(first, second)
    assert _transport_protocol(first) == "v1"
    assert _transport_protocol(second) == "v1"
