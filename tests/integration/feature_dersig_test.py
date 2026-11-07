# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_dersig`, one body per test over each node.

Read from Core's `test/functional/feature_dersig.py` (`fa6f7e36a8c2`,
2026-10-23), on the option and MiniWallet families together
([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)):
`-testactivationheight=dersig@N` (`Capability.TEST_ACTIVATION_HEIGHT`)
holds BIP66 inactive until a chosen height, and `MiniWallet.generate`
(`Capability.MINE`) mines to it with no node wallet.

Core's own closing checks are kept too, in a body of their own
([ISS 167](https://github.com/btclib-org/functional-tests/issues/167)):
a spend whose signature is not strict DER is mined in the block before
BIP66 activates, and from the configured height on is refused by
`testmempoolaccept` and `submitblock`, each in Core's own wording, while
the same spend DER-encoded is accepted. That needs a real ECDSA spend,
which `MiniWallet`'s own `ADDRESS_OP_TRUE` coins do not carry: its
coins are coinbases paying `RAW_P2PK_SCRIPT_PUB_KEY`, Core's own
`RAW_P2PK` output, spent under `raw_p2pk_script_sig` (`mini_wallet.py`),
at Core's own height so that they have matured. Dropped from it is the
debug-log wording of the block refusal, which `submitblock`'s own answer
already carries.

Kept, and read at the bitcoind release `node-integration.yml` pins rather
than assumed: `getdeploymentinfo`'s own `bip66` entry, which transitions
from inactive to active one block before the configured height -- the
same "not active as of current tip, but the next block must obey rules"
boundary Core's own file comments -- and the buried-deployment version
floor Core's own file also checks with a version-2 block once BIP66 is
active, `bad-version(0x00000002)` both in `submitblock`'s own answer and
in the node's own debug log (`ProcessNewBlock: AcceptBlock FAILED
(bad-version(0x00000002), ...)`, `src/validation.cpp`'s own
`ContextualCheckBlockHeader`). Not narrowed further than Core's own file
on this specific check: measured live against that release, a version-2
block is refused the same way both before and after BIP66's own
configured height, BIP65's own default floor (`-testactivationheight`
touching only the deployment it names) already requiring version 4 from
height 1 -- so Core's own file does not attempt the before/after
comparison for this check either, only the after-activation refusal.

Each `feature_dersig_<node>_test.py` module beside this one runs each
body, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from btclib.script.script import parse, serialize
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.mini_wallet import (
    FEE,
    RAW_P2PK_SCRIPT_PUB_KEY,
    MiniWallet,
    build_next_block,
    raw_p2pk_script_sig,
)
from tests.integration.script_verify_flag_test import block_script_verify_flag_failed

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.block.block import Block
    from btclib.script.script_pub_key import ScriptPubKey

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_block_below_the_minimum_version_is_logged",
    "a_block_below_the_minimum_version_is_refused",
    "a_non_der_signature_is_refused_once_active",
    "dersig_activates_one_block_before_the_configured_height",
]

# Core's own file hardcodes 102; this harness needs only that the height is
# reached in a handful of mined blocks, not that it matches Core's own value
_DERSIG_HEIGHT = 12

# Core's own height, which the signed spends below need: two coinbases
# paying `RAW_P2PK_SCRIPT_PUB_KEY` matured by the time the chain is two
# blocks short of it
_SIGNED_DERSIG_HEIGHT = COINBASE_MATURITY + 2


def _start_with_dersig_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    height: int = _DERSIG_HEIGHT,
) -> NodeAdapter:
    """Return one node, restarted with BIP66 held back until `height`."""
    (node,) = cluster(1)
    require(Capability.TEST_ACTIVATION_HEIGHT, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart([f"-testactivationheight=dersig@{height}"])
    return node


def _block(
    node: NodeAdapter,
    script_pub_key: ScriptPubKey,
    *,
    version: int = 4,
    transactions: tuple[Tx, ...] = (),
) -> Block:
    """Return `build_next_block`'s own block, at Core's own default version.

    :param node: the node whose own tip this block extends.
    :param script_pub_key: what the coinbase pays.
    :param version: the block header's own version; `4`, Core's own
        `create_block` default, where not given.
    :param transactions: what the block carries beside its coinbase.
    """
    return build_next_block(node, script_pub_key, transactions, version=version)


def _submit(node: NodeAdapter, block: Block) -> object:
    """Return `submitblock`'s own answer for `block`."""
    return node.rpc.call("submitblock", [block.serialize(check_validity=False).hex()])


def _spend(coinbase: Tx, *, der: bool = True) -> Tx:
    """Return a signed spend of `coinbase`, a `RAW_P2PK_SCRIPT_PUB_KEY` coin.

    Where `der` is false the signature is Core's own `unDERify`'d one: a
    zero byte after S, ahead of the hash type, which BIP66's strict DER
    refuses and the lax parse before it does not.
    """
    tx_out = TxOut(coinbase.vout[0].value - FEE, RAW_P2PK_SCRIPT_PUB_KEY)
    unsigned = Tx(
        version=2, lock_time=0, vin=[TxIn(OutPoint(coinbase.id, 0))], vout=[tx_out]
    )
    script_sig = raw_p2pk_script_sig(unsigned, 0)
    if not der:
        (signature_hex,) = parse(script_sig)
        assert isinstance(signature_hex, str)  # `parse` answers a push as hex
        signature = bytes.fromhex(signature_hex)
        script_sig = serialize([signature[:-1] + b"\0" + signature[-1:]])
    tx_in = TxIn(OutPoint(coinbase.id, 0), script_sig=script_sig)
    return Tx(version=2, lock_time=0, vin=[tx_in], vout=[tx_out])


def _bip66(node: NodeAdapter) -> dict[str, object]:
    """Return `getdeploymentinfo`'s own `bip66` entry."""
    bip66: dict[str, object] = node.rpc.call("getdeploymentinfo")["deployments"][
        "bip66"
    ]
    return bip66


def dersig_activates_one_block_before_the_configured_height(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `getdeploymentinfo`'s `bip66` entry tracks the configured height.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_dersig_height(cluster, skip_counts)
    wallet = MiniWallet(node)
    assert _bip66(node) == {
        "type": "buried",
        "active": False,
        "height": _DERSIG_HEIGHT,
    }

    wallet.generate(_DERSIG_HEIGHT - 2)
    assert _bip66(node)["active"] is False

    wallet.generate(1)  # tip is now one block before the configured height
    assert _bip66(node)["active"] is True


def a_block_below_the_minimum_version_is_refused(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check that once BIP66 is active, a version-2 block never becomes the tip.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_dersig_height(cluster, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(_DERSIG_HEIGHT - 1)
    old_tip = node.rpc.call("getbestblockhash")

    block = _block(node, wallet.script_pub_key, version=2)
    assert _submit(node, block) == "bad-version(0x00000002)"
    assert node.rpc.call("getbestblockhash") == old_tip


def a_block_below_the_minimum_version_is_logged(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check the same refusal, in bitcoind's own debug log wording.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    :raises TypeError: the node declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    node = _start_with_dersig_height(cluster, skip_counts)
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    wallet = MiniWallet(node)
    wallet.generate(_DERSIG_HEIGHT - 1)

    block = _block(node, wallet.script_pub_key, version=2)
    with assert_debug_log(node.debug_log_path, ["bad-version(0x00000002)"]):
        _submit(node, block)


def a_non_der_signature_is_refused_once_active(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check a non-DER signature is mined before BIP66, and refused from it on.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    node = _start_with_dersig_height(cluster, skip_counts, _SIGNED_DERSIG_HEIGHT)
    coinbases = []
    for _ in range(2):
        block = _block(node, RAW_P2PK_SCRIPT_PUB_KEY)
        assert _submit(node, block) is None
        coinbases.append(block.transactions[0])
    MiniWallet(node).generate(_SIGNED_DERSIG_HEIGHT - 4)
    assert node.rpc.call("getblockcount") == _SIGNED_DERSIG_HEIGHT - 2
    assert _bip66(node)["active"] is False

    # a non-DER signature can still appear in the block before activation
    block = _block(
        node, RAW_P2PK_SCRIPT_PUB_KEY, transactions=(_spend(coinbases[0], der=False),)
    )
    assert _submit(node, block) is None
    assert node.rpc.call("getbestblockhash") == block.header.hash.hex()
    assert _bip66(node)["active"] is True
    tip = block.header.hash.hex()

    # from the configured height on, the mempool refuses it for that alone
    spend = _spend(coinbases[1], der=False)
    txid = spend.id.hex()
    reason = "mempool-script-verify-flag-failed (Non-canonical DER signature)"
    details = (
        f"{reason}, input 0 of {txid} (wtxid {txid}), "
        f"spending {coinbases[1].id.hex()}:0"
    )
    answer = node.rpc.call(
        "testmempoolaccept",
        [[spend.serialize(False, check_validity=False).hex()], 0],
    )
    assert answer == [
        {
            "txid": txid,
            "wtxid": txid,
            "allowed": False,
            "reject-reason": reason,
            "reject-details": details,
        }
    ]

    # and so does a block
    block = _block(node, RAW_P2PK_SCRIPT_PUB_KEY, transactions=(spend,))
    answer = _submit(node, block)
    wording = block_script_verify_flag_failed(node)
    assert answer == f"{wording} (Non-canonical DER signature)"
    assert node.rpc.call("getbestblockhash") == tip

    # while the same spend, DER-encoded, is accepted
    block = _block(node, RAW_P2PK_SCRIPT_PUB_KEY, transactions=(_spend(coinbases[1]),))
    assert _submit(node, block) is None
    assert node.rpc.call("getbestblockhash") == block.header.hash.hex()
