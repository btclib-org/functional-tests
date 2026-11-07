# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_scanblocks`, one body over each node.

Read from Core's `test/functional/rpc_scanblocks.py` (`aeca0610865e`,
2026-07-01), the option and MiniWallet families together
([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)):
`-blockfilterindex=1` (`Capability.BLOCK_FILTER_INDEX`) keeps BIP158's
basic filter per block, `scanblocks` answers from it, and
`MiniWallet.send_to` (`Capability.MINE`) pays the two outputs the scans
look for.

Core's own claim in full, in Core's own order: a scan by address and by
ranged `pkh()` descriptor, `start_height` and `stop_height` bounding it,
`filter_false_positives` either way, a precomputed false positive
colliding with the regtest genesis block's coinbase output, the second
node started without the index refusing a scan, and every argument error
Core's file asserts. Four spellings change. Core's `generate` mines the
mempool node-side and flushes the validation queue before returning;
this mines the two payments client-side, `MiniWallet.generate`'s own
`confirm` naming them, and then waits for the index to reach the tip.
Core's `getnewdestination` is a random key's P2TR output here too, built
by `ScriptPubKey.p2tr`. Core checks its false positive with
`bip158_basic_element_hash` over the scripts `getblock` reports; this
builds the genesis block's own filter with `BasicBlockFilter.from_block`
and asks it to `match` both scripts, the single-element filter matching
a script exactly where that script's ranged hash equals the coinbase
output's. Core's refusal of a null `scanobjects` is its `master`'s
wording, from the pinned commit on; a release before `v32.0` refuses the
same call with a type error, and either refusal passes, each with its
own code.

The second node is independent of the first -- never connected, never
synced -- and asked for nothing but the refusal, so it is its own test
here rather than a second adapter inside the first.

Each test takes a cluster and starts one node of its own, asks it for
`Capability.BLOCK_FILTER_INDEX` before anything else, and the first
restarts it with `-blockfilterindex=1` before any block is mined. Each
`rpc_scanblocks_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import secrets
import time
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError
from btclib.block.block import Block
from btclib.block.block_filter import BasicBlockFilter
from btclib.key import PrvKeyData
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx.limits import COINBASE_MATURITY
from ecc.curves import secp256k1

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "scanblocks_finds_the_blocks_paying_what_it_is_asked_for",
    "scanblocks_refuses_without_the_index",
]

_COIN = 100_000_000

# Core's own constants: an extended public key, and the P2PKH address of
# its child 5, which a ranged `pkh()` descriptor over it covers
_PARENT_KEY = (
    "tpubD6NzVbkrYhZ4WaWSyoBvQwbpLkojyoTZPRsgXELWz3Popb3qkjcJyJUGLnL4"
    "qHHoQvao8ESaAstxYSnhyswJ76uZPStJRJCTKvosUCJZL5B"
)
_CHILD_5_ADDRESS = "mkS4HXoTYWRTescLGaUTGbtTTYX5EjJyEE"

# Core's own precomputed false positive: a P2WPKH script whose BIP158
# ranged hash, under the regtest genesis block's key, equals that of the
# genesis coinbase output's own script
_FALSE_POSITIVE_SPK = bytes.fromhex("001400000000000000000000000000000000000cadcb")

# `scanblocks "start" null`'s own refusal: Core's `master` since
# `aeca0610865e` checks for the missing argument before reading it, and
# a release before `v32.0` reads it as an array first and refuses the
# null's type
_NULL_SCANOBJECTS_MASTER = "scanobjects argument is required for the start action"
_NULL_SCANOBJECTS_RELEASE = "JSON value of type null is not of expected type array"


def _wait_until_indexes_synced(adapter: NodeAdapter, timeout: float = 30) -> None:
    """Wait until every index has caught up with the node's own tip.

    Core's own `generate` returns only once `sync_mempools` has run
    `syncwithvalidationinterfacequeue`, which is what hands a new block to
    the index; this waits for the effect instead, `getindexinfo`'s own
    `best_block_height` reaching `getblockcount`. `synced` alone is not
    that: it turns true once the index's initial sync is done, and stays
    true while the index is still a block behind.
    """
    deadline = time.monotonic() + scaled(timeout)
    while True:
        tip = adapter.rpc.call("getblockcount")
        indexes = adapter.rpc.call("getindexinfo")
        if all(
            index["synced"] and index["best_block_height"] == tip
            for index in indexes.values()
        ):
            return
        if time.monotonic() > deadline:
            err_msg = f"an index was still behind tip {tip}: {indexes!r}"
            raise TimeoutError(err_msg)
        time.sleep(0.1)


def _relevant_blocks(adapter: NodeAdapter, *params: object) -> list[str]:
    relevant: list[str] = adapter.rpc.call("scanblocks", ["start", *params])[
        "relevant_blocks"
    ]
    return relevant


def scanblocks_finds_the_blocks_paying_what_it_is_asked_for(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check Core's own `run_test`, the node without the index excepted.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.BLOCK_FILTER_INDEX, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart(["-blockfilterindex=1"])
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 1)

    key = PrvKeyData(secrets.randbelow(secp256k1.n - 1) + 1, "regtest")
    spk_1 = ScriptPubKey.p2tr(key.pub, network="regtest")
    addr_1 = spk_1.address
    paid_1 = wallet.send_to(spk_1, 1 * _COIN)
    paid_5 = wallet.send_to(ScriptPubKey.from_address(_CHILD_5_ADDRESS), 1 * _COIN)

    # mine a block and assure that the mined blockhash is in the result
    blockhash = wallet.generate(1, confirm=[paid_1, paid_5])[0].hex()
    height = node.rpc.call("getblockheader", [blockhash])["height"]
    _wait_until_indexes_synced(node)

    out = node.rpc.call("scanblocks", ["start", [f"addr({addr_1})"]])
    assert blockhash in out["relevant_blocks"]
    assert out["to_height"] == height
    assert out["from_height"] == 0
    assert out["completed"] is True

    # mine another block
    blockhash_new = wallet.generate(1)[0].hex()
    height_new = node.rpc.call("getblockheader", [blockhash_new])["height"]
    _wait_until_indexes_synced(node)

    # start_height at the block just mined excludes the first one
    assert blockhash not in _relevant_blocks(node, [f"addr({addr_1})"], height_new)
    # start_height at the first mined block includes it
    assert blockhash in _relevant_blocks(node, [f"addr({addr_1})"], height)
    for filter_false_positives in (False, True):
        assert blockhash in _relevant_blocks(
            node,
            [f"addr({addr_1})"],
            height,
            None,
            "basic",
            {"filter_false_positives": filter_false_positives},
        )
    # the stop height includes it, and excludes it one block earlier
    assert blockhash in _relevant_blocks(node, [f"addr({addr_1})"], height, height)
    assert blockhash not in _relevant_blocks(node, [f"addr({addr_1})"], 0, height - 1)
    # a ranged descriptor covering child 5 finds the second payment
    assert blockhash in _relevant_blocks(
        node, [{"desc": f"pkh({_PARENT_KEY}/*)", "range": [0, 100]}], height
    )

    # a precomputed false positive, colliding with genesis's coinbase
    genesis_blockhash = node.rpc.call("getblockhash", [0])
    # the header's own check defaults to mainnet's proof-of-work limit
    genesis = Block.parse(
        bytes.fromhex(node.rpc.call("getblock", [genesis_blockhash, 0])),
        check_validity=False,
    )
    genesis_filter = BasicBlockFilter.from_block(genesis, [])
    assert genesis_filter.element_count == 1
    genesis_coinbase_spk = genesis.transactions[0].vout[0].script_pub_key.script
    assert genesis_filter.match(genesis_coinbase_spk)
    assert genesis_filter.match(_FALSE_POSITIVE_SPK)
    coinbase_desc = {"desc": f"raw({genesis_coinbase_spk.hex()})"}
    false_positive_desc = {"desc": f"raw({_FALSE_POSITIVE_SPK.hex()})"}
    assert genesis_blockhash in _relevant_blocks(node, [coinbase_desc], 0, 0)
    assert genesis_blockhash in _relevant_blocks(node, [false_positive_desc], 0, 0)
    # filter_false_positives drops the false positive, and only it
    exact = {"filter_false_positives": True}
    assert genesis_blockhash in _relevant_blocks(
        node, [coinbase_desc], 0, 0, "basic", exact
    )
    assert genesis_blockhash not in _relevant_blocks(
        node, [false_positive_desc], 0, 0, "basic", exact
    )

    with pytest.raises(RpcError, match="Unknown filtertype") as excinfo:
        _relevant_blocks(node, [f"addr({addr_1})"], 0, 10, "extended")
    assert excinfo.value.code == -5
    for heights, message in (
        ((100_000_000,), "Invalid start_height"),
        ((10, 0), "Invalid stop_height"),
        ((10, 100_000_000), "Invalid stop_height"),
    ):
        with pytest.raises(RpcError, match=message) as excinfo:
            _relevant_blocks(node, [f"addr({addr_1})"], *heights)
        assert excinfo.value.code == -1

    # no scan is running: the status is empty, and there is none to abort
    assert node.rpc.call("scanblocks", ["status"]) is None
    assert node.rpc.call("scanblocks", ["abort"]) is False
    with pytest.raises(RpcError, match="Invalid action 'foobar'") as excinfo:
        node.rpc.call("scanblocks", ["foobar"])
    assert excinfo.value.code == -8
    # null scanobjects: Core's `master` names the missing argument, and
    # a release before `v32.0` refuses the null as a type error
    with pytest.raises(RpcError) as excinfo:
        node.rpc.call("scanblocks", ["start", None])
    refusal = excinfo.value
    assert (refusal.code == -1 and _NULL_SCANOBJECTS_MASTER in str(refusal)) or (
        refusal.code == -3 and _NULL_SCANOBJECTS_RELEASE in str(refusal)
    )


def scanblocks_refuses_without_the_index(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check Core's second node, with no `-blockfilterindex`, refuses a scan.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.BLOCK_FILTER_INDEX, node.capabilities, skip_counts)
    key = PrvKeyData(secrets.randbelow(secp256k1.n - 1) + 1, "regtest")
    addr = ScriptPubKey.p2tr(key.pub, network="regtest").address
    with pytest.raises(
        RpcError, match="Index is not enabled for filtertype basic"
    ) as excinfo:
        node.rpc.call("scanblocks", ["start", [f"addr({addr})"]])
    assert excinfo.value.code == -1
