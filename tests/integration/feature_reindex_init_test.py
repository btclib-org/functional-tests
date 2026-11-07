# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_reindex_init`, one body over each node.

Read from Core's `test/functional/feature_reindex_init.py`
(`0d1301b47a35`, 2026-03-24), the option and disk families together
([ISS 14](https://github.com/btclib-org/functional-tests/issues/14)):
a node stopped and its block index removed from `blocks/index` refuses
to start, advising a reindex in Core's own words, and started again
with `-test=reindex_after_failure_noninteractive_yes`
(`Capability.REINDEX_AFTER_FAILURE`) reindexes from its block files
back to the height it had. `blocks/` is Core's own layout
(`Capability.BLK_FILES`), under the regtest chain's own directory, the
chain every node of this suite starts on by default.

Core's own claim in full but for the refusal's stderr, read per-build
below. Its chain is the framework's cached one; this node mines its own
to the same height (`Capability.MINE`). The node is built over a data
directory of its own, through `make_adapter`
(`tests/integration/conftest.py`), since the test removes a directory
inside it. Core's harness waits, after a start, for `getmempoolinfo`'s
`loaded`, "for the node to finish reindex, block import, and loading the
mempool" (`TestNode.wait_for_rpc_connection`'s own comment), where
`NodeAdapter.start` returns on the first RPC answer. This waits the same
way before reading the height: without the wait, `getblockcount` answered
short of that height right after the start, measured against the release
`node-integration.yml` pins.

A bitcoind before `v31.0` prints `": "` ahead of that message, `noui`
having added the empty caption and its separator
(bitcoin/bitcoin#34276). No probe tells the two apart, the node having no
option or RPC for it: that build is asserted to print it, read off its own
`getnetworkinfo` `version`
([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).

Each `feature_reindex_init_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import os
import re
import shutil
from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import free_ports, wait_until
from tests.integration.script_verify_flag_test import bitcoind_version

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = ["a_lost_block_index_is_rebuilt_once_allowed"]

# the height of Core's own cached chain
_HEIGHT = 200

# how many blocks one `mine` call asks for, as `rpc_getblockfrompeer_test`
# chunks them: a bitcoind's own `generatetoaddress` answers only once every
# block is mined, so one call has to finish inside the RPC client's own
# timeout ([ISS 226](https://github.com/btclib-org/functional-tests/issues/226))
_MINE_CHUNK = 50

# Core's own `expected_msg`
_NO_BLOCK_INDEX = (
    f"Error initializing block database.{os.linesep}"
    "Please restart with -reindex or -reindex-chainstate to recover."
)

# Core's own `CLIENT_VERSION`, at or past which a refused start's message
# has no `": "` ahead of it: `v31.0`'s (bitcoin/bitcoin#34276). A known
# limit: a `master` build from that change's merge (`6ae96ed607`,
# 2026-01-28) until the version moved to `31.99` (`48b952cbb6`, 2026-03-06)
# reports `309900` and prints the bare message all the same, so this test
# fails against such a build
_BARE_MESSAGE_VERSION = 310000

# `_wait_for_rpc`'s own wording (`node.py`), split into the exit code and
# the stderr it carries
_EARLY_EXIT = re.compile(
    r"node process exited with (-?\d+) before its RPC answered -- stderr: (.*)",
    re.DOTALL,
)


def _mempool_loaded(node: NodeAdapter) -> bool:
    """Return `getmempoolinfo`'s own `loaded`."""
    info = node.rpc.call("getmempoolinfo")
    assert isinstance(info, dict)
    return info["loaded"] is True


def a_lost_block_index_is_rebuilt_once_allowed(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check a start refuses a lost block index, and one allowed reindexes.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    datadir = tmp_path / "datadir"
    rpc_port, p2p_port = free_ports(2)
    node = make_adapter(cls, executable, datadir, rpc_port, p2p_port)
    require(Capability.REINDEX_AFTER_FAILURE, node.capabilities, skip_counts)
    require(Capability.BLK_FILES, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    try:
        node.start()
        version = bitcoind_version(node)
        bare = version is None or version >= _BARE_MESSAGE_VERSION
        for _ in range(_HEIGHT // _MINE_CHUNK):
            node.mine(_MINE_CHUNK)
        node.stop()

        shutil.rmtree(datadir / "regtest" / "blocks" / "index")
        with pytest.raises(RuntimeError) as refused:
            node.start()
        early_exit = _EARLY_EXIT.fullmatch(str(refused.value))
        assert early_exit is not None
        assert int(early_exit[1]) != 0
        assert early_exit[2].strip() == (
            _NO_BLOCK_INDEX if bare else f": {_NO_BLOCK_INDEX}"
        )

        node.restart(["-test=reindex_after_failure_noninteractive_yes"])
        wait_until(lambda: _mempool_loaded(node))
        assert node.rpc.call("getblockcount") == _HEIGHT
    finally:
        node.stop()
