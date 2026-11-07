# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_presegwit_node_upgrade`, one body over each node.

Read from Core's `test/functional/feature_presegwit_node_upgrade.py`
(`fad7bd9ba3eef03fcdd7cb17011ea0c6e483c767`, 2026-01-14) and ported,
an option-family test
([ISS 3](https://github.com/btclib-org/functional-tests/issues/3)):
`-testactivationheight=segwit@N` (`Capability.TEST_ACTIVATION_HEIGHT`)
holds segwit inactive until a chosen height, blocks are mined below it
over `submitblock` (`Capability.MINE`), and the node is then restarted
with a lower height its chain already runs past. The node starts without
the option and is restarted with Core's own first height before any
block is mined.

Every assertion of Core's own file is kept: a fresh chain with segwit
inactive; the mined height; the restart refused, its exit code non-zero
and its whole stderr equal to Core's own `expected_msg` -- Core's own
`ErrorMatch.FULL_TEXT`, the default `assert_start_raises_init_error`
applies; and, restarted with `-reindex` added, a chain one block short of
the lower height with segwit active. Dropped is the empty stderr Core's
own `TestNode.stop_node` expects of every stop: a check its harness makes
around each stop rather than one this file makes, and `NodeAdapter.stop`
makes it for no test.

The blocks are mined in the shape Core's own `generate` gives them rather
than `MiniWallet.generate`'s: a coinbase carrying the witness commitment
and no witness nonce. `GenerateCoinbaseCommitment` (`src/validation.cpp`)
writes the commitment into every block `CreateNewBlock`
(`src/node/miner.cpp`) assembles, and its own
`UpdateUncommittedBlockStructures` adds the nonce only once segwit is
active, so every block Core's own file mines carries the one without the
other. Under segwit's rules `CheckWitnessMalleation` refuses that pair
(`bad-witness-nonce-size`), which is what stops the reindexed chain one
block short of the lower height. A `MiniWallet` block carries neither,
and a block committing to no witness and carrying none is valid either
side of activation: measured against the bitcoind release
`node-integration.yml` pins, a chain of them reindexes to its full mined
height instead.

`NodeAdapter.start` (`node.py`) waits for the RPC to answer and no
longer, where Core's own `TestNode.wait_for_rpc_connection` also waits
for `getmempoolinfo`'s `loaded`, which the import thread of
`src/init.cpp` sets only once `ImportBlocks` has returned: this body
waits for it after the reindexing start, the one start here after which
a height it reads could still be moved by a running import.

A bitcoind before `v31.0` prints `": "` ahead of that message, `noui`
having added the empty caption and its separator
(bitcoin/bitcoin#34276). No probe tells the two apart, the node having no
option or RPC for it: that build is asserted to print it, read off its own
`getnetworkinfo` `version`
([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).

Each `feature_presegwit_node_upgrade_<node>_test.py` module beside this
one runs it, `tests/integration/conftest.py`'s own module docstring
having how.
"""

from __future__ import annotations

import os
import re
import time
from typing import TYPE_CHECKING

import pytest
from btclib.block.block import witness_commitment_output

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet, build_next_block
from btclib_functional_tests.timeout_factor import scaled
from tests.integration.script_verify_flag_test import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.block.block import Block

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["a_pre_segwit_chain_needs_a_reindex_to_upgrade"]

# Core's own three values: the activation height the node first runs
# with, how many blocks are mined below it, and the lower height the
# restart names
_SEGWIT_HEIGHT = 10
_MINED = 8
_LOWER_SEGWIT_HEIGHT = 5

# Core's own `expected_msg`, `os.linesep` included
_EXPECTED_MSG = (
    f"Witness data for blocks after height {_LOWER_SEGWIT_HEIGHT} requires "
    f"validation. Please restart with -reindex..{os.linesep}"
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

# Core's own `TestNode.wait_until` default, scaled as Core scales it
_IMPORT_TIMEOUT = 60.0

# BIP141's reserved value as Core's own `GenerateCoinbaseCommitment` fills
# it, the commitment's second preimage half
_ZERO_WITNESS_NONCE = b"\x00" * 32

# the commitment of a block carrying its coinbase alone:
# `coinbase_witness_commitment` puts BIP141's all-zero placeholder where the
# coinbase's own wtxid would sit, so that commitment reads no transaction
_COINBASE_ONLY_COMMITMENT = witness_commitment_output(
    (), _ZERO_WITNESS_NONCE
).script_pub_key


def _uncommitted_block(node: NodeAdapter, wallet: MiniWallet) -> Block:
    """Return a solved block on `node`'s own tip, shaped as Core mines it.

    Its coinbase carries the witness commitment and no witness nonce,
    this module's own docstring has why. Built here rather than through
    `MiniWallet.generate`, whose blocks carry neither.

    :param node: the node whose own tip this block extends.
    :param wallet: whose own `script_pub_key` the coinbase pays.
    """
    return build_next_block(
        node, wallet.script_pub_key, extra_output_script=_COINBASE_ONLY_COMMITMENT
    )


def _wait_for_import(node: NodeAdapter) -> None:
    """Wait for `getmempoolinfo`'s `loaded`, as Core's own start does.

    :param node: the node whose own block import is awaited.
    :raises TimeoutError: the mempool never reported loaded.
    """
    timeout = scaled(_IMPORT_TIMEOUT)
    deadline = time.monotonic() + timeout
    while not node.rpc.call("getmempoolinfo")["loaded"]:
        if time.monotonic() >= deadline:
            err_msg = f"the block import did not finish within {timeout} s"
            raise TimeoutError(err_msg)
        time.sleep(0.1)


def _segwit_active(node: NodeAdapter) -> bool:
    """Core's own `softfork_active(node, "segwit")` (`util.py`).

    :param node: the node asked.
    """
    deployments = node.rpc.call("getdeploymentinfo")["deployments"]
    return bool(deployments["segwit"]["active"])


def a_pre_segwit_chain_needs_a_reindex_to_upgrade(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check a lower segwit height refuses to start until `-reindex` is added.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.TEST_ACTIVATION_HEIGHT, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart([f"-testactivationheight=segwit@{_SEGWIT_HEIGHT}"])
    assert node.rpc.call("getblockcount") == 0
    assert not _segwit_active(node)

    wallet = MiniWallet(node)
    for _ in range(_MINED):
        block = _uncommitted_block(node, wallet)
        answer = node.rpc.call(
            "submitblock", [block.serialize(check_validity=False).hex()]
        )
        assert answer is None
    assert node.rpc.call("getblockcount") == _MINED

    version = bitcoind_version(node)
    bare = version is None or version >= _BARE_MESSAGE_VERSION
    node.stop()
    lower = f"-testactivationheight=segwit@{_LOWER_SEGWIT_HEIGHT}"
    with pytest.raises(RuntimeError) as refused:
        node.restart([lower])
    early_exit = _EARLY_EXIT.fullmatch(str(refused.value))
    assert early_exit is not None
    assert int(early_exit[1]) != 0
    assert early_exit[2] == (_EXPECTED_MSG if bare else f": {_EXPECTED_MSG}")

    node.restart(["-reindex", lower])
    _wait_for_import(node)
    assert node.rpc.call("getblockcount") == _LOWER_SEGWIT_HEIGHT - 1
    assert _segwit_active(node)
