# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_prune_stale_fork`, one body over each node.

Read from Core's `test/functional/feature_prune_stale_fork.py`
(`3f44f9aef7cc`, 2026-05-12): a node under `-prune=1` and `-fastprune`
(`Capability.FASTPRUNE`) is given a two-block stale fork, the parent's
header alone over `submitheader` and the child in full over
`submitblock`. It then mines and prunes until the child's own block data
is gone, restarts, and mines one more block. `-prune` asks for no
capability, as `rpc_getblockfrompeer_test.py`'s own docstring has.

`build_fork` (`mini_wallet.py`) stands in for Core's `create_empty_fork`
(`blocktools.py`), each block's coinbase paying Core's own bare
`OP_TRUE`. Core's node starts at height 200, on the framework's cached
chain; this one mines to that height first, as
`feature_remove_pruned_files_on_startup_test.py` does. Every block but
the fork's two is mined with `generatetoaddress` (`Capability.GENERATE`)
to Core's own first deterministic address, `_MINE_CHUNK` at a time.

A bitcoind that `_carries_the_fix` says lacks bitcoin/bitcoin#35168, the
fix for bitcoin/bitcoin#35050, is not restarted: the restart is Core's
subject, and checks that fix
([ISS 62](https://github.com/btclib-org/functional-tests/issues/62)).

The node is built over a data directory of its own, through
`make_adapter` (`tests/integration/conftest.py`), since it starts with
`-prune` and `-fastprune`. Each `feature_prune_stale_fork_<node>_test.py`
module beside this one runs it, `tests/integration/conftest.py`'s own
module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError
from btclib.script.script_pub_key import ScriptPubKey

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import build_fork
from btclib_functional_tests.node import free_ports
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = ["a_pruned_stale_fork_survives_a_restart"]

# Core's own `extra_args`
_OPTIONS = ("-prune=1", "-fastprune")

# Core's own first deterministic address, `TestNode.PRIV_KEYS`
# (`test_framework/test_node.py`), the one its `generate` pays
_ADDRESS = "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z"

# Core's `create_coinbase` default scriptPubKey, a bare `OP_TRUE`
_OP_TRUE = ScriptPubKey(b"\x51", check_validity=False)

# the height Core's node starts at, on the framework's cached chain; how
# many blocks Core mines over the fork; and how far below the tip it
# prunes
_CACHE_HEIGHT = 200
_BLOCKS = 500
_KEEP = 100

# how many blocks one `generatetoaddress` call asks for: it answers only
# once every block is mined, and has to inside the RPC client's own
# timeout (`timeout_factor.rpc_client_timeout`)
_MINE_CHUNK = 50

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, bounding the builds that keep a pruned
# block out of `m_blocks_unlinked` on startup (bitcoin/bitcoin#35168):
# `31.2` and later `31.x`, the backport (bitcoin/bitcoin#36662,
# `f2fe67f586`) preceding the bump to `31.2` (`cfde8285f9`); and `32.0`
# and later, `v32.0rc1` being the first `32.x` tag carrying the fix
# (`fb47793b99`, merged 2026-06-10 while `master` read `31.99`). No `29.x`
# or `30.x` tag carries it. A known limit: a `master` build from that
# merge up to the move to `32.99` (`f3fec67c3e`, 2026-09-11) reports
# `319900` with the fix, and this test does not restart it
_BACKPORT_VERSION = 310200
_MASTER_31_99_VERSION = 319900
_FIX_VERSION = 320000


def _mine(node: NodeAdapter, count: int) -> None:
    """Mine `count` blocks to `_ADDRESS`, at most `_MINE_CHUNK` per call."""
    while count > 0:
        node.rpc.call("generatetoaddress", [min(count, _MINE_CHUNK), _ADDRESS])
        count -= _MINE_CHUNK


def _carries_the_fix(version: int | None) -> bool:
    """Say whether a node of this `bitcoind_version` carries the fix.

    `None`, a node other than bitcoind, is read as carrying it.
    """
    return (
        version is None
        or version >= _FIX_VERSION
        or _BACKPORT_VERSION <= version < _MASTER_31_99_VERSION
    )


def _n_tx(node: NodeAdapter, block_hash: str) -> int:
    """Return `getblockheader`'s own `nTx` for `block_hash`."""
    header = node.rpc.call("getblockheader", [block_hash])
    assert isinstance(header, dict)
    n_tx = header["nTx"]
    assert isinstance(n_tx, int)
    return n_tx


def a_pruned_stale_fork_survives_a_restart(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check a node restarts and mines once a stale fork's child is pruned.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    rpc_port, p2p_port = free_ports(2)
    node = make_adapter(
        cls, executable, tmp_path / "datadir", rpc_port, p2p_port, _OPTIONS
    )
    require(Capability.FASTPRUNE, node.capabilities, skip_counts)
    require(Capability.GENERATE, node.capabilities, skip_counts)
    try:
        node.start()
        version = bitcoind_version(node)
        _mine(node, _CACHE_HEIGHT)

        side_parent, side_child = build_fork(node, _OP_TRUE, 2)
        parent_hash = side_parent.header.hash.hex()
        child_hash = side_child.header.hash.hex()
        node.rpc.call(
            "submitheader", [side_parent.header.serialize(check_validity=False).hex()]
        )
        node.rpc.call("submitblock", [side_child.serialize(check_validity=False).hex()])
        assert _n_tx(node, parent_hash) == 0
        assert _n_tx(node, child_hash) == 1

        _mine(node, _BLOCKS)
        node.rpc.call("pruneblockchain", [node.rpc.call("getblockcount") - _KEEP])
        with pytest.raises(RpcError) as excinfo:
            node.rpc.call("getblock", [child_hash])
        assert excinfo.value.code == -1
        assert "Block not available (pruned data)" in str(excinfo.value)

        if not _carries_the_fix(version):
            return
        height = node.rpc.call("getblockcount")
        node.restart()
        _mine(node, 1)
        assert node.rpc.call("getblockcount") == height + 1
    finally:
        node.stop()
