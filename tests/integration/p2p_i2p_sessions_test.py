# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `p2p_i2p_sessions`, one body over each node.

Read from Core's `test/functional/p2p_i2p_sessions.py` (`fa5f29774872`,
2025-12-16), a file of
[ISS 47](https://github.com/btclib-org/functional-tests/issues/47):
a node given `-i2psam` dials an I2P address over a persistent SAM
session where `-i2pacceptincoming=1` and over a transient one where
`-i2pacceptincoming=0`. Both of Core's steps are kept, in its order,
each reading Core's own `i2p` line out of the node's debug log, so the
test asks for `Capability.I2P_SAM` and `Capability.DEBUG_LOG`.

Each line is read once, as soon as `addnode` returns, which is Core's own
`assert_debug_log` default. It matters for the persistent session: a node
given `-i2pacceptincoming=1` also tries to open that session on its own,
again and again while the bridge is missing, and each try writes the same
line (`CConnman::ThreadI2PAcceptIncoming`, `src/net.cpp`), so a log
polled for longer finds the line whether or not the dial wrote it.

Nothing listens at the `-i2psam` endpoint, which is what Core's file
assumes of its own. Core names a fixed port, `60000`; the endpoint here
is a port `node.free_port` hands out, bound and closed, and both nodes
are given it, as Core gives both its own.

Core's two nodes are not linked to each other here, where Core's
`setup_network` links them: neither step reads the other node.

A bitcoind before `v31.0` logs `Creating persistent SAM session`, without
`I2P` (bitcoin/bitcoin#34051). No probe tells the two apart, the node
having no option or RPC for it: that build is asked for its own line, read
off its own `getnetworkinfo` `version`
([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).

Each `p2p_i2p_sessions_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import free_port
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["i2pacceptincoming_chooses_a_persistent_or_a_transient_session"]

# Core's own address
_ADDRESS = "zsxwyo6qcn3chqzwxnseusqgsnuw3maqnztkiypyfxtya4snkoka.b32.i2p"

# Core's own `CLIENT_VERSION`, at or past which the session lines name `I2P`:
# `v31.0`'s (bitcoin/bitcoin#34051). A known limit: a `master` build from
# that change's merge (`2210feb446`, 2025-12-14) until the version moved to
# `31.99` (`48b952cbb6`, 2026-03-06) reports `309900` and logs the newer line
# all the same, so this test fails against such a build
_I2P_NAMED_VERSION = 310000


def i2pacceptincoming_chooses_a_persistent_or_a_transient_session(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `-i2pacceptincoming` decides which SAM session a dial opens.

    Core's own `run_test`: `addnode`'s `onetry` to one I2P address, from
    a node given `-i2pacceptincoming=1` and then from one given
    `-i2pacceptincoming=0`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    :raises TypeError: a node declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    node0, node1 = cluster(2)
    require(Capability.I2P_SAM, node0.capabilities, skip_counts)
    require(Capability.DEBUG_LOG, node0.capabilities, skip_counts)
    if not isinstance(node0, BitcoindAdapter) or not isinstance(node1, BitcoindAdapter):
        err_msg = f"{type(node0).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    version = bitcoind_version(node0)
    i2p = "" if version is not None and version < _I2P_NAMED_VERSION else "I2P "
    i2psam = f"-i2psam=127.0.0.1:{free_port()}"
    node0.restart([i2psam, "-i2pacceptincoming=1"])
    node1.restart([i2psam, "-i2pacceptincoming=0"])

    with assert_debug_log(
        node0.debug_log_path, [f"Creating persistent {i2p}SAM session"], timeout=0
    ):
        node0.rpc.call("addnode", [_ADDRESS, "onetry"])

    with assert_debug_log(
        node1.debug_log_path, [f"Creating transient {i2p}SAM session"], timeout=0
    ):
        node1.rpc.call("addnode", [_ADDRESS, "onetry"])
