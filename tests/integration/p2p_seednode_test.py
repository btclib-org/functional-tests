# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_seednode`, one body over each node.

Read from Core's `test/functional/p2p_seednode.py` (`fa5f29774872`,
2025-12-16), a file of
[ISS 47](https://github.com/btclib-org/functional-tests/issues/47):
a node given no `-seednode` asks none, one whose address table is empty
asks its `-seednode` at once, and one whose table holds addresses dials
those first and asks its `-seednode` only once its wait has passed.
Every step of Core's `run_test` is kept, in its order, over one node,
each reading Core's own lines out of the node's debug log, those it
expects and those it does not.

The test asks for `Capability.ADDRESS_FETCH`, `Capability.KNOWN_ADDRESSES`
for Core's `addpeeraddress`, `Capability.PROXY` for the unreachable
proxy, `Capability.CLOCK` for `setmocktime` and `Capability.DEBUG_LOG`.

No peer answers. Every start is given Core's `UNREACHABLE_PROXY_ARG`
(`test_framework/netutil.py`), a `-proxy` nothing listens at, so every
address the node dials and every seed node it asks is reached through a
proxy that is not there.

The node is built with Core's own `extra_args` and started with them
from its first start, as Core's `set_test_params` has it, and every
restart is given them, with a `-seednode` where Core's `restart_node`
adds one. Core's `write_config` (`test_framework/util.py`) also writes
`dnsseed=0` into the node's `bitcoin.conf`, so `-dnsseed=0` joins every
start here: through a proxy, a regtest node asks for its chain's own
`dummySeed.invalid.` otherwise. Its `fixedseeds=0` line is not passed,
regtest having no fixed seeds (`CRegTestParams`,
`src/kernel/chainparams.cpp`). It writes no `connect=0`, Core's file
turning `disable_autoconnect` off, and `BitcoindAdapter` passes no
`-connect` on regtest either.

Core's harness starts its node with `-v2transport=0` unless its own
`--v2transport` is given, where this one leaves bitcoind's own default.
The `trying v1 connection` line holds
under either: `addpeeraddress` keeps an address with no `NODE_P2P_V2`
service (`src/rpc/net.cpp`), and `CConnman::ThreadOpenConnections`
(`src/net.cpp`) dials v2 only an address that has it.

The node starts on a fresh chain, where Core's, its `setup_clean_chain`
left unset, starts on its cached one; no step reads the chain. Its ports
come from `node.free_ports`, where Core derives them from its own index.
Each log wait counts from the end of its block, as `assert_debug_log`
(`debug_log.py`) counts it, where Core's counts from its start.

Core's last step also passes with its `setmocktime` removed, here and
under Core's own framework alike (measured against the release
`node-integration.yml` pins): the node's own wait then passes on the
real clock, inside the step's.

Each `p2p_seednode_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import random
import time
from typing import TYPE_CHECKING

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import free_ports

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = ["a_seed_node_is_asked_at_once_only_by_an_empty_address_table"]

# Core's `UNREACHABLE_PROXY_ARG` (`test_framework/netutil.py`)
_UNREACHABLE_PROXY = "-proxy=127.0.0.1:1"

# `write_config`'s own `dnsseed=0` line (`test_framework/util.py`)
_DNSSEED_OFF = "-dnsseed=0"

# Core's own `extra_args`, and `write_config`'s `dnsseed=0`
_EXTRA_ARGS = (_UNREACHABLE_PROXY, _DNSSEED_OFF)

# Core's own `ADD_NEXT_SEEDNODE`
_ADD_NEXT_SEEDNODE = 10

_EMPTY_ADDRMAN = "Empty addrman, adding seednode"

_NO_PEER_FROM_ADDRMAN = (
    f"Couldn't connect to peers from addrman after {_ADD_NEXT_SEEDNODE} "
    "seconds. Adding seednode"
)


def _no_seednode(node: BitcoindAdapter) -> None:
    """Core's `test_no_seednode`: no `-seednode`, none asked, no wait."""
    with assert_debug_log(
        node.debug_log_path,
        [],
        [_EMPTY_ADDRMAN, _NO_PEER_FROM_ADDRMAN],
        timeout=_ADD_NEXT_SEEDNODE,
    ):
        node.restart()


def _seednode_empty_addrman(node: BitcoindAdapter) -> None:
    """Core's `test_seednode_empty_addrman`: the seed node is asked at once."""
    seed_node = "25.0.0.1"
    with assert_debug_log(
        node.debug_log_path,
        [f"{_EMPTY_ADDRMAN} ({seed_node}) to addrfetch"],
        timeout=_ADD_NEXT_SEEDNODE,
    ):
        node.restart([*_EXTRA_ARGS, f"-seednode={seed_node}"])


def _seednode_non_empty_addrman(node: BitcoindAdapter) -> None:
    """Core's `test_seednode_non_empty_addrman`: the seed node waits.

    The table's addresses are dialled first, and the seed node is asked
    once the clock passes the wait.
    """
    seed_node = "25.0.0.2"
    rpc = node.rpc
    # Core's own unreachable addresses, filling the table
    for i in range(10):
        ip = (
            f"{random.randrange(128, 169)}.{random.randrange(1, 255)}."
            f"{random.randrange(1, 255)}.{random.randrange(1, 255)}"
        )
        port = 8333 + i
        rpc.call("addpeeraddress", [ip, port])

    with assert_debug_log(
        node.debug_log_path, ["trying v1 connection"], timeout=_ADD_NEXT_SEEDNODE
    ):
        node.restart([*_EXTRA_ARGS, f"-seednode={seed_node}"])

    with assert_debug_log(
        node.debug_log_path,
        [f"{_NO_PEER_FROM_ADDRMAN} ({seed_node}) to addrfetch"],
        [_EMPTY_ADDRMAN],
        timeout=_ADD_NEXT_SEEDNODE * 1.5,
    ):
        node.rpc.call("setmocktime", [int(time.time()) + _ADD_NEXT_SEEDNODE + 1])


def a_seed_node_is_asked_at_once_only_by_an_empty_address_table(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check when a node asks its `-seednode` for addresses.

    Core's own `run_test`, its three steps in its order.

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
        Capability.ADDRESS_FETCH,
        Capability.KNOWN_ADDRESSES,
        Capability.PROXY,
        Capability.CLOCK,
        Capability.DEBUG_LOG,
    ):
        require(capability, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    try:
        node.start()
        _no_seednode(node)
        _seednode_empty_addrman(node)
        _seednode_non_empty_addrman(node)
    finally:
        node.stop()
