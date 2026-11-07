# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_dumptxoutset`, one body over each node.

Read from Core's `test/functional/rpc_dumptxoutset.py` (`58eeab790d98`,
2026-05-13), the clock and disk families together
([ISS 14](https://github.com/btclib-org/functional-tests/issues/14)):
a node whose clock is set to just past its genesis block
(`Capability.CLOCK`) mines `COINBASE_MATURITY` blocks
(`Capability.GENERATE`) and writes its UTXO set to a file in its chain
directory over `dumptxoutset` (`Capability.DUMP_UTXO_SET`), answering
the coins it wrote, the block the set is at and its height, the file's
absolute path, the set's own hash and the chain's transaction count. It
refuses a path already holding a file, a path in a directory that does
not exist and a snapshot type it does not know, each with Core's own
code and message. Then `invalidateblock` (`Capability.INVALIDATE_BLOCK`)
and blocks mined under the wall clock give it a fork below its tip,
`reconsiderblock` puts it back on its own chain, and a
`rollback` dump at the fork's height is at that height and at the block
the node's own chain has there, with Core's `in_memory` option and
without.

Not every build dumps at a height a fork also reaches: a release before
`v32.0` refuses to, the fork's own block taking the tip once the node
rolls back, so the caller says which the running node is expected to do,
and the refusal is asserted with the build's own code and message.

Core asserts the hashes as constants: the block at the tip, the file's
own SHA256 and `txoutset_hash`. Each depends on the coinbase the build
writes, and Core's own file carries different constants at `v30.3`
than at the pin. This asserts each against what the same node reports:
the block `getblockhash` answers at the tip's height; the file's
metadata naming that block, the regtest network and the coins written,
and a second dump of the same chain writing the same bytes; and
`gettxoutsetinfo`'s own `hash_serialized_3`.

Every block pays Core's own first deterministic address,
`TestNode.PRIV_KEYS`'s own, the one Core's `generate` pays.

The node is built over a data directory of its own, through
`make_adapter` (`tests/integration/conftest.py`), since the test reads
the file the node writes inside it.

Each `rpc_dumptxoutset_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError, magic_from_chain
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import free_ports, wait_until

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = ["the_utxo_set_is_dumped"]

# Core's own first deterministic address, `TestNode.PRIV_KEYS`
# (`test_framework/test_node.py`), the one its `generate` pays
_ADDRESS = "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z"

# Core's own `FILENAME`
_FILE_NAME = "txoutset.dat"

# `RPC_MISC_ERROR` and `RPC_INVALID_PARAMETER`, Core's own
# `src/rpc/protocol.h`
_RPC_MISC_ERROR = -1
_RPC_INVALID_PARAMETER = -8

# how far below the tip Core's fork starts, and how many blocks it holds
_FORK_DEPTH = 10
_FORK_BLOCKS = 2

# Core's own `SNAPSHOT_MAGIC_BYTES` (`src/node/utxo_snapshot.h`), which
# the file's `SnapshotMetadata` opens with, a two-byte version following
_SNAPSHOT_MAGIC = b"utxo\xff"
_NETWORK_MAGIC_OFFSET = len(_SNAPSHOT_MAGIC) + 2


def _refused(
    node: NodeAdapter,
    params: list[object],
    message: str,
    code: int = _RPC_INVALID_PARAMETER,
) -> None:
    """Assert `dumptxoutset` refuses `params`, as Core's assertion asserts it.

    :param message: a substring of the node's own error message.
    :param code: the node's own error code.
    """
    with pytest.raises(RpcError) as refusal:
        node.rpc.call("dumptxoutset", params)
    assert refusal.value.code == code, str(refusal.value)
    assert message in refusal.value.args[0], str(refusal.value)


def _dump(node: NodeAdapter, params: list[object]) -> dict[str, object]:
    """Return `dumptxoutset`'s own answer to `params`."""
    out = node.rpc.call("dumptxoutset", params)
    assert isinstance(out, dict)
    return out


def _metadata_matches(snapshot: bytes, base_hash: str, coins: int) -> None:
    """Assert `snapshot`'s own `SnapshotMetadata` names `base_hash` and `coins`.

    The magic, the regtest network's own message start, the block the set
    is at in its internal byte order, and the coin count as eight
    little-endian bytes: `SnapshotMetadata::Serialize`
    (`src/node/utxo_snapshot.h`), the version between the two magics left
    unread.
    """
    assert snapshot.startswith(_SNAPSHOT_MAGIC)
    network_magic = magic_from_chain("regtest")
    hash_offset = _NETWORK_MAGIC_OFFSET + len(network_magic)
    coins_offset = hash_offset + 32
    assert snapshot[_NETWORK_MAGIC_OFFSET:hash_offset] == network_magic
    assert snapshot[hash_offset:coins_offset] == bytes.fromhex(base_hash)[::-1]
    assert snapshot[coins_offset : coins_offset + 8] == coins.to_bytes(8, "little")


def _dumps_across_a_fork(node: NodeAdapter, *, rolls_back: bool) -> None:
    """Dump at a forked height, Core's own `test_dumptxoutset_with_fork`.

    :param node: the node, its chain `COINBASE_MATURITY` blocks tall.
    :param rolls_back: whether the build dumps at that height, as the
        pinned file asserts, or refuses to, as a release before `v32.0`
        does.
    """
    tip = node.rpc.call("getbestblockhash")
    target_height = node.rpc.call("getblockcount") - _FORK_DEPTH
    target_hash = node.rpc.call("getblockhash", [target_height])

    invalid_block = node.rpc.call("getblockhash", [target_height + 1])
    node.rpc.call("invalidateblock", [invalid_block])
    # Core's own reason: the wall clock, not to mine the same blocks again
    node.set_mock_time(0)
    node.rpc.call("generatetoaddress", [_FORK_BLOCKS, _ADDRESS])

    node.rpc.call("reconsiderblock", [invalid_block])
    wait_until(lambda: node.rpc.call("getbestblockhash") == tip)

    dumps: tuple[list[object], ...] = (
        ["txoutset_fork.dat", "rollback", {"rollback": target_height}],
        [
            "txoutset_fork_mem.dat",
            "rollback",
            {"rollback": target_height, "in_memory": True},
        ],
    )
    for params in dumps:
        if not rolls_back:
            _refused(
                node,
                params,
                "Could not roll back to requested height.",
                _RPC_MISC_ERROR,
            )
            continue
        out = _dump(node, params)
        assert out["base_height"] == target_height
        assert out["base_hash"] == target_hash


def the_utxo_set_is_dumped(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
    rolls_back_past_a_fork: Callable[[NodeAdapter], bool],
) -> None:
    """Check `dumptxoutset` writes the set, refuses bad paths, and rolls back.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    :param rolls_back_past_a_fork: whether the running build dumps at a
        height a fork of its chain also reaches.
    """
    datadir = tmp_path / "datadir"
    rpc_port, p2p_port = free_ports(2)
    node = make_adapter(cls, executable, datadir, rpc_port, p2p_port)
    for capability in (
        Capability.DUMP_UTXO_SET,
        Capability.CLOCK,
        Capability.GENERATE,
        Capability.INVALIDATE_BLOCK,
    ):
        require(capability, node.capabilities, skip_counts)
    chain_dir = datadir / "regtest"
    try:
        node.start()
        genesis = node.rpc.call("getblockheader", [node.rpc.call("getblockhash", [0])])
        node.set_mock_time(genesis["time"] + 1)
        node.rpc.call("generatetoaddress", [COINBASE_MATURITY, _ADDRESS])

        out = _dump(node, [_FILE_NAME, "latest"])
        expected_path = chain_dir / _FILE_NAME

        assert expected_path.is_file()

        assert out["coins_written"] == COINBASE_MATURITY
        assert out["base_height"] == COINBASE_MATURITY
        assert out["path"] == str(expected_path)
        # Core's constant, where this reads the node's own block
        base_hash = node.rpc.call("getblockhash", [COINBASE_MATURITY])
        assert out["base_hash"] == base_hash

        # Core's file hash, where this reads the file's metadata and
        # dumps the same chain again
        snapshot = expected_path.read_bytes()
        _metadata_matches(snapshot, base_hash, COINBASE_MATURITY)
        again = _dump(node, ["txoutset_again.dat", "latest"])
        assert (chain_dir / "txoutset_again.dat").read_bytes() == snapshot
        assert again["txoutset_hash"] == out["txoutset_hash"]

        # Core's constant, where this reads the node's own UTXO set hash
        utxo_set = node.rpc.call("gettxoutsetinfo")
        assert out["txoutset_hash"] == utxo_set["hash_serialized_3"]
        assert out["nchaintx"] == COINBASE_MATURITY + 1

        _refused(node, [_FILE_NAME, "latest"], f"{_FILE_NAME} already exists")
        invalid_path = datadir / "invalid" / "path"
        _refused(
            node,
            [str(invalid_path), "latest"],
            f"Couldn't open file {invalid_path}.incomplete for writing",
        )

        _refused(
            node,
            ["utxos.dat", "bogus"],
            'Invalid snapshot type "bogus" specified. '
            'Please specify "rollback" or "latest"',
        )

        _dumps_across_a_fork(node, rolls_back=rolls_back_past_a_fork(node))
    finally:
        node.stop()
