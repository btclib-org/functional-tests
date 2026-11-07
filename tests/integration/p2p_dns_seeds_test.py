# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_dns_seeds`, one body over each node.

Read from Core's `test/functional/p2p_dns_seeds.py` (`fa4cb96bdec2`,
2026-02-17), a file of
[ISS 47](https://github.com/btclib-org/functional-tests/issues/47):
a node given `-connect` asks its chain's DNS seeds for no peers unless
`-dnsseed` says so, refuses `-forcednsseed` beside `-dnsseed` off, skips
the seeds once outbound full-relay peers connect but not block-relay-only
ones, queries them at once under `-forcednsseed`, and waits before
querying for as long as the size of its address table decides. Every
step of Core's `run_test` is kept, in its order, over one node, each
reading Core's own line out of the node's debug log or, for a start
Core expects refused, Core's own wording whole out of its stderr.

The test asks for `Capability.DNS_SEED`, `Capability.KNOWN_ADDRESSES`
for Core's `addpeeraddress` and `getnodeaddresses`, `Capability.PROXY`
for the unreachable proxy, `Capability.TYPED_OUTBOUND` for the peers
the node dials and `Capability.DEBUG_LOG`.

No DNS server is involved. Every start but the refused ones is given
Core's `UNREACHABLE_PROXY_ARG` (`test_framework/netutil.py`), a
`-proxy` nothing listens at: under a proxy, a node querying a DNS seed
resolves nothing itself and queues a connection to the seed's name
through that proxy instead (`CConnman::ThreadDNSAddressSeed`,
`src/net.cpp`). The peers the node dials are on loopback, an address
no proxy is used for.

The node is built with Core's own `extra_args` and started with them
from its first start, as Core's `set_test_params` has it, and a restart
naming none goes back to them, as Core's `restart_node(0)` does. Core's
`write_config` (`test_framework/util.py`) also writes `connect=0` into
the node's `bitcoin.conf`, so `-connect=0` joins every start here whose
own arguments name no `-connect`. Its `dnsseed=0` line is not passed:
under it, Core's own run finds `DNS seeding disabled` and the
`-forcednsseed` refusal beside `-connect` whatever `-connect` does, where
here each comes from `-connect`'s own soft-set of `-dnsseed`, the reason
Core's comments give. Its `fixedseeds=0` line bears on no start here,
the node drawing no peer of its own under either `-connect`.

The node starts on a fresh chain, as Core's `setup_clean_chain` has it,
so no cached chain is read. Its ports come from `node.free_ports`, and
each peer it dials listens on a port the OS chooses, where Core derives
each from `p2p_idx`. The peers are closed before the next restart,
where Core's `restart_node` forgets them. Each log wait counts from the
end of its block, as `assert_debug_log` (`debug_log.py`) counts it,
where Core's counts from its start.

Core's `force_dns_test` step also passes with its `-forcednsseed`
removed, here and under Core's own framework alike (measured against
the release `node-integration.yml` pins): the node already knows an
address, so it queries the seeds after its shorter wait, which ends
inside the step's own.

Each `p2p_dns_seeds_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import itertools
import re
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import magic_from_chain

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import free_ports
from btclib_functional_tests.peer import Listener, Peer

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = ["dns_seeds_are_queried_only_when_peers_are_wanting"]

# Core's `UNREACHABLE_PROXY_ARG` (`test_framework/netutil.py`)
_UNREACHABLE_PROXY = "-proxy=127.0.0.1:1"

# `write_config`'s own `connect=0` line (`test_framework/util.py`)
_CONNECT_OFF = "-connect=0"

# Core's own `extra_args`, and `write_config`'s `connect=0`
_EXTRA_ARGS = ("-dnsseed=1", _UNREACHABLE_PROXY, _CONNECT_OFF)

_FAKE_ADDR = "fakenodeaddr.fakedomain.invalid."

_FORCEDNSSEED_REFUSED = (
    "Error: Cannot set -forcednsseed to true when setting -dnsseed to false."
)

# `_wait_for_rpc`'s own wording (`node.py`), split into the exit code and
# the stderr it carries
_EARLY_EXIT = re.compile(
    r"node process exited with (-?\d+) before its RPC answered -- stderr: (.*)",
    re.DOTALL,
)


def _refusal(node: NodeAdapter, extra_args: list[str]) -> str:
    """Start `node` with `extra_args`, and return the stderr it exits with.

    Core's own `assert_start_raises_init_error`: the start fails, with an
    exit code other than `0`, before its RPC ever answers.

    :param node: a node the caller has stopped.
    :param extra_args: what to start it with.
    """
    with pytest.raises(RuntimeError) as refused:
        node.restart(extra_args)
    early_exit = _EARLY_EXIT.fullmatch(str(refused.value))
    assert early_exit is not None
    assert int(early_exit[1]) != 0
    return early_exit[2].strip()


def _add_outbound(node: NodeAdapter, connection_type: str) -> Peer:
    """Have `node` dial a fresh listener as `connection_type`, and shake hands.

    Core's `add_outbound_p2p_connection`, whose own wait ends on the
    `verack` and a `sync_with_ping`.
    """
    with Listener(magic_from_chain(node.chain)) as listener:
        node.add_outbound_connection(listener.address, connection_type)
        peer = listener.accept()
    peer.handshake()
    peer.sync_with_ping()
    return peer


def _init_arg_tests(node: BitcoindAdapter) -> None:
    """Core's `init_arg_tests`: `-connect`, `-dnsseed` and `-forcednsseed`."""
    node.stop()
    with assert_debug_log(node.debug_log_path, ["DNS seeding disabled"], timeout=2):
        node.restart([f"-connect={_FAKE_ADDR}", _UNREACHABLE_PROXY])

    with assert_debug_log(
        node.debug_log_path, ["Loading addresses from DNS seed"], timeout=12
    ):
        node.restart([f"-connect={_FAKE_ADDR}", "-dnsseed=1", _UNREACHABLE_PROXY])

    node.stop()
    refused = _refusal(node, ["-forcednsseed=1", "-dnsseed=0", _CONNECT_OFF])
    assert refused == _FORCEDNSSEED_REFUSED

    node.stop()
    refused = _refusal(node, ["-forcednsseed=1", f"-connect={_FAKE_ADDR}"])
    assert refused == _FORCEDNSSEED_REFUSED

    node.restart()


def _existing_outbound_connections_test(node: BitcoindAdapter) -> None:
    """Core's `existing_outbound_connections_test`.

    Outbound full-relay peers connect, and the seeds are skipped.
    """
    node.rpc.call("addpeeraddress", ["192.0.0.8", 8333])

    node.restart()
    peers: list[Peer] = []
    try:
        with assert_debug_log(
            node.debug_log_path,
            ["P2P peers available. Skipped DNS seeding."],
            timeout=12,
        ):
            peers.extend(_add_outbound(node, "outbound-full-relay") for _ in range(2))
    finally:
        for peer in peers:
            peer.close()


def _existing_block_relay_connections_test(node: BitcoindAdapter) -> None:
    """Core's `existing_block_relay_connections_test`.

    Block-relay-only peers connect, and the seeds are queried anyway.
    """
    node.rpc.call("addpeeraddress", ["192.0.0.8", 8333])

    node.restart()
    peers: list[Peer] = []
    try:
        with assert_debug_log(
            node.debug_log_path, ["Loading addresses from DNS seed"], timeout=12
        ):
            peers.extend(_add_outbound(node, "block-relay-only") for _ in range(2))
    finally:
        for peer in peers:
            peer.close()


def _force_dns_test(node: BitcoindAdapter) -> None:
    """Core's `force_dns_test`: `-forcednsseed` queries the seeds at once."""
    with assert_debug_log(
        node.debug_log_path, ["Loading addresses from DNS seed"], timeout=12
    ):
        node.restart(["-forcednsseed", "-dnsseed=1", _UNREACHABLE_PROXY, _CONNECT_OFF])

    node.restart()


def _wait_time_tests(node: BitcoindAdapter) -> None:
    """Core's `wait_time_tests`: a wait the table's size decides."""
    rpc = node.rpc
    for i in range(5):
        rpc.call("addpeeraddress", [f"192.0.0.{i}", 8333])

    with assert_debug_log(
        node.debug_log_path,
        ["Waiting 11 seconds before querying DNS seeds.\n"],
        timeout=2,
    ):
        node.restart()

    rpc = node.rpc
    for i in itertools.count():
        first_octet = i % 2 + 1
        second_octet = i % 256
        third_octet = i % 100
        rpc.call(
            "addpeeraddress", [f"{first_octet}.{second_octet}.{third_octet}.1", 8333]
        )
        # Core's own periodic read: the table's size is not the number of
        # calls, an address added into a full bucket displacing another
        if i > 1000 and i % 100 == 0:
            addresses = rpc.call("getnodeaddresses", [0])
            assert isinstance(addresses, list)
            if len(addresses) > 1000:
                break

    with assert_debug_log(
        node.debug_log_path,
        ["Waiting 300 seconds before querying DNS seeds.\n"],
        timeout=2,
    ):
        node.restart()


def dns_seeds_are_queried_only_when_peers_are_wanting(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check when a node queries its DNS seeds, and how long it waits first.

    Core's own `run_test`, its five steps in its order.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    :raises TypeError: the node declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    rpc_port, p2p_port = free_ports(2)
    node = make_adapter(
        cls, executable, tmp_path / "node", rpc_port, p2p_port, _EXTRA_ARGS
    )
    for capability in (
        Capability.DNS_SEED,
        Capability.KNOWN_ADDRESSES,
        Capability.PROXY,
        Capability.TYPED_OUTBOUND,
        Capability.DEBUG_LOG,
    ):
        require(capability, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    try:
        node.start()
        _init_arg_tests(node)
        _existing_outbound_connections_test(node)
        _existing_block_relay_connections_test(node)
        _force_dns_test(node)
        _wait_time_tests(node)
    finally:
        node.stop()
