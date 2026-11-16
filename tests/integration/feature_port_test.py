# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_port`, one body over each node.

Read from Core's `test/functional/feature_port.py`
(`997757dd2b4d`, 2024-11-15), the option and log families together
([ISS 14](https://github.com/btclib-org/functional-tests/issues/14)):
where a node restarted with `-port` and `-bind`
(`Capability.LISTEN_ADDRESS`) listens, read off the `Bound to` lines its
debug log (`Capability.DEBUG_LOG`) gains, and the error a `-port` of
`65536` or `0` stops its start with. Every check of Core's is kept, in
its order.

Core keeps its framework's own `-bind` entries off the node:
`bind_to_localhost_only`'s `bind=127.0.0.1` and the pair
`TestNode.start` adds wherever no `-bind` is given
(`test_framework/test_node.py`). An adapter's own `_command` may name
one too, so the node is built from the class the caller passes,
`UnboundBitcoindAdapter` (`support/bitcoind_adapters/`) for bitcoind. Every
start names a `-port`, so the node never listens at its chain's default
port.

Core's ports come from `p2p_port`, and the port after each is used
unprobed. Here each port comes from `free_ports` together with the port
after it, both probed.

A refused start compares the node's stderr with Core's `expected_msg`
whole, the `ErrorMatch.FULL_TEXT` comparison
`assert_start_raises_init_error` makes by default.

Each `feature_port_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import free_port, free_ports

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = ["port_and_bind_decide_where_the_node_listens"]

# `_wait_for_rpc`'s own wording (`node.py`), split into the exit code and
# the stderr it carries
_EARLY_EXIT = re.compile(
    r"node process exited with (-?\d+) before its RPC answered -- stderr: (.*)",
    re.DOTALL,
)

# how many draws `_port_and_next` makes before giving up
_DRAWS = 16


def _port_and_next() -> int:
    """Return a port `free_ports` answers together with the port after it."""
    for _ in range(_DRAWS):
        port, following = free_ports(2)
        if following == port + 1:
            return port
    err_msg = f"no port free together with the next one in {_DRAWS} draws"
    raise RuntimeError(err_msg)


def _refusal(node: BitcoindAdapter, extra_args: list[str]) -> str:
    """Start `node` with `extra_args`, and return the stderr it exits with.

    Core's own `assert_start_raises_init_error`: the start fails, with an
    exit code other than `0`, before its RPC ever answers.

    :param node: the node to start.
    :param extra_args: what to start it with.
    """
    with pytest.raises(RuntimeError) as refused:
        node.restart(extra_args)
    early_exit = _EARLY_EXIT.fullmatch(str(refused.value))
    assert early_exit is not None
    assert int(early_exit[1]) != 0
    return early_exit[2].strip()


def port_and_bind_decide_where_the_node_listens(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check where `-port` and `-bind` have the node listen, and a bad port.

    :param make_adapter: the session's own adapter factory.
    :param cls: the class to build the node from.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    :raises TypeError: the node declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    rpc_port = free_port()
    port1 = _port_and_next()
    port2 = _port_and_next()
    node = make_adapter(cls, executable, tmp_path / "node", rpc_port, port1)
    require(Capability.LISTEN_ADDRESS, node.capabilities, skip_counts)
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    log = node.debug_log_path
    try:
        # `-port` alone: every address at it, and the onion bind after it
        with assert_debug_log(
            log, [f"Bound to 0.0.0.0:{port1}", f"Bound to 127.0.0.1:{port1 + 1}"]
        ):
            node.restart(["-listen", f"-port={port1}"])

        # `-port` twice: the last one given
        with assert_debug_log(
            log,
            [f"Bound to 0.0.0.0:{port2}", f"Bound to 127.0.0.1:{port2 + 1}"],
            [f"Bound to 0.0.0.0:{port1}"],
        ):
            node.restart(["-listen", f"-port={port1}", f"-port={port2}"])

        # `-bind` naming a port: `-port` ignored
        with assert_debug_log(
            log, [f"Bound to 0.0.0.0:{port2}"], [f"Bound to 0.0.0.0:{port1}"]
        ):
            node.restart(["-listen", f"-port={port1}", f"-bind=0.0.0.0:{port2}"])

        # `-bind` naming no port: `-port`'s
        with assert_debug_log(log, [f"Bound to 0.0.0.0:{port1}"]):
            node.restart(["-listen", f"-port={port1}", "-bind=0.0.0.0"])

        # an onion bind naming no port: the one after `-port`'s
        with assert_debug_log(log, [f"Bound to 127.0.0.1:{port1 + 1}"]):
            node.restart(["-listen", f"-port={port1}", "-bind=127.0.0.1=onion"])

        node.stop()
        for port in ("65536", "0"):
            assert _refusal(node, ["-listen", f"-port={port}"]) == (
                f"Error: Invalid port specified in -port: '{port}'"
            )
    finally:
        node.stop()
