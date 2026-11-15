# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `mempool_datacarrier`, one body per test over each node.

Read from Core's `test/functional/mempool_datacarrier.py` (`fa5f29774872`,
2025-12-16) and narrowed to what the option and MiniWallet families reach
together
([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)):
`-datacarrier` and `-datacarriersize` (`Capability.DATACARRIER`) gate
whether, and how large, an `OP_RETURN` output is relayed, and
`MiniWallet.create_self_transfer` (`Capability.MINE`) is what funds the
transaction that carries one, `mini_wallet.py`'s own
`nulldata_script_pub_key` building the output itself -- unlike
`ScriptPubKey.nulldata`, refusing no length, since a size limit is
exactly what a node's own `-datacarriersize` is being asked about here.

A smaller claim than Core's own file: kept are the default (uncapped)
setting, `-datacarrier=0` (no relay of any nulldata output, whatever its
size), a custom `-datacarriersize` at its own boundary, and the default
`permitbaremultisig` that `getmempoolinfo` reports, which Core's own file
checks here "rather than create a new test for it"; dropped is
Core's own fourth node (`-datacarriersize=2`) and its own full sweep of
`None`/zero-byte/one-byte payloads across every node, none of which
exercises a boundary the first three configurations do not already
cover.

Each body starts its node without the options it is about and restarts
it with them, where it names any, before any block is mined.

`v30.0rc1` is the first tag to carry both changes below. A build
without bitcoin/bitcoin#32406 caps a null-data output at 83 bytes by
default, relays one such output per transaction, and refuses a null-data
output with `scriptpubkey`, not `datacarrier`; there each body asserts
that policy. `getmempoolinfo` reports `permitbaremultisig` and
`maxdatacarriersize` only on a build with bitcoin/bitcoin#29954: where
the key is absent, `datacarriersize_bounds_the_payload` does not read it
and `bare_multisig_is_permitted_by_default` asks `testmempoolaccept`
about a bare multisig output instead.

This module also exports `mempool_info_reports` and `uncapped_by_default`,
which `mempool_accept_test.py` reads the same builds with.

Each `mempool_datacarrier_<node>_test.py` module beside this one runs
each body, `tests/integration/conftest.py`'s own module docstring having
how.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError
from btclib.key import PrvKeyData
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import Tx, TxOut
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet, nulldata_script_pub_key
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "bare_multisig_is_permitted_by_default",
    "datacarrier_disabled_refuses_any_null_data_output",
    "datacarriersize_bounds_the_payload",
    "default_settings_allow_a_large_op_return",
    "mempool_info_reports",
    "uncapped_by_default",
]

# Core's own historical default -datacarriersize, "now used to test
# coverage" (its own file's comment) rather than any longer the default
_CUSTOM_DATACARRIER_SIZE = 83

# the default `-datacarriersize` of a build without bitcoin/bitcoin#32406
_CAPPED_DEFAULT_SIZE = 83

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v30.0`, whose first tag,
# `v30.0rc1`, is the first to carry bitcoin/bitcoin#32406: no default cap on
# a null-data output, several of them per transaction, and the `datacarrier`
# reject reason. The policy is how `Capability.DATACARRIER` behaves, not
# whether a build has it, so the body reads it off the node's RPC
# (`capability.py`'s docstring). A known limit: a `master` build from that
# merge (`f3bbc74664`) up to the move to `30.99` (`9f744fffc3`) reports `299900`
# with the new policy, so this test fails against such a build
_UNCAPPED_VERSION = 300000


def mempool_info_reports(node: NodeAdapter, key: str) -> bool:
    """Whether `node`'s `getmempoolinfo` carries `key`, read off its `help`.

    `permitbaremultisig` and `maxdatacarriersize` come with
    bitcoin/bitcoin#29954, of which `v30.0rc1` is the first tag.
    """
    text = node.rpc.call("help", ["getmempoolinfo"])
    assert isinstance(text, str)
    return f'"{key}"' in text


def uncapped_by_default(node: NodeAdapter) -> bool:
    """Whether `node` has bitcoin/bitcoin#32406's null-data policy.

    Any node but a bitcoind answers yes, as `bitcoind_version` has none.
    """
    version = bitcoind_version(node)
    return version is None or version >= _UNCAPPED_VERSION


def _refusal(node: NodeAdapter) -> str:
    """Return the reject reason of a null-data output the policy refuses."""
    return "datacarrier" if uncapped_by_default(node) else "scriptpubkey"


def _hex(tx: Tx) -> str:
    """Return `tx` serialized with its witness."""
    return tx.serialize(True, check_validity=False).hex()


def _nulldata_tx(wallet: MiniWallet, size: int) -> Tx:
    """Return a self-transfer carrying one `size`-byte null-data output."""
    tx = wallet.create_self_transfer()
    tx.vout.append(TxOut(0, nulldata_script_pub_key(b"\xaa" * size)))
    return tx


def _send(node: NodeAdapter, tx: Tx) -> None:
    """Send `tx` and check the mempool holds it."""
    node.rpc.call("sendrawtransaction", [_hex(tx)])
    assert tx.id.hex() in node.rpc.call("getrawmempool")


def _assert_refused(node: NodeAdapter, tx: Tx) -> None:
    """Check `node` refuses `tx` with its own null-data reject reason."""
    with pytest.raises(RpcError, match=_refusal(node)):
        node.rpc.call("sendrawtransaction", [_hex(tx)])


def default_settings_allow_a_large_op_return(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check what a null-data output relays with no `-datacarrier*` argument.

    A build without bitcoin/bitcoin#32406 caps the output at 83 bytes by
    default: there a payload at the cap relays and one byte more, like a
    sizeable one, is refused with `scriptpubkey`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.DATACARRIER, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 3)
    if uncapped_by_default(node):
        _send(node, _nulldata_tx(wallet, 500))
        return
    at_cap = _nulldata_tx(wallet, _CAPPED_DEFAULT_SIZE - 3)
    _send(node, at_cap)
    for size in (_CAPPED_DEFAULT_SIZE - 2, 500):
        _assert_refused(node, _nulldata_tx(wallet, size))


def datacarrier_disabled_refuses_any_null_data_output(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check `-datacarrier=0` refuses relay of a null-data output of any size.

    A build without bitcoin/bitcoin#32406 words the refusal `scriptpubkey`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.DATACARRIER, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart(["-datacarrier=0"])
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 1)
    _assert_refused(node, _nulldata_tx(wallet, 0))


def datacarriersize_bounds_the_payload(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check a payload at the configured size relays; one byte more is refused.

    `getmempoolinfo`'s `maxdatacarriersize` is read where the build has it;
    a build without bitcoin/bitcoin#32406 words the refusal `scriptpubkey`.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.DATACARRIER, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    node.restart(["-datacarrier=1", f"-datacarriersize={_CUSTOM_DATACARRIER_SIZE}"])
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 1)
    if mempool_info_reports(node, "maxdatacarriersize"):
        info = node.rpc.call("getmempoolinfo")
        assert isinstance(info, dict)
        assert info["maxdatacarriersize"] == _CUSTOM_DATACARRIER_SIZE

    # OP_RETURN (1) + one-byte pushdata length (2) + data
    at_limit = _nulldata_tx(wallet, _CUSTOM_DATACARRIER_SIZE - 3)
    _send(node, at_limit)
    _assert_refused(node, _nulldata_tx(wallet, _CUSTOM_DATACARRIER_SIZE - 2))


def bare_multisig_is_permitted_by_default(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Check that with no `-permitbaremultisig`, bare multisig is permitted.

    `getmempoolinfo` says so where the build reports `permitbaremultisig`;
    otherwise `testmempoolaccept` allows a transaction paying a bare
    multisig output.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.PERMIT_BARE_MULTISIG, node.capabilities, skip_counts)
    if mempool_info_reports(node, "permitbaremultisig"):
        info = node.rpc.call("getmempoolinfo")
        assert isinstance(info, dict)
        assert info["permitbaremultisig"] is True
        return
    require(Capability.MINE, node.capabilities, skip_counts)
    wallet = MiniWallet(node)
    wallet.generate(COINBASE_MATURITY + 1)
    tx = wallet.create_self_transfer()
    pub_key = PrvKeyData(1, network="regtest").pub
    tx.vout[0] = TxOut(tx.vout[0].value, ScriptPubKey.p2ms(1, [pub_key] * 3))
    (verdict,) = node.rpc.call("testmempoolaccept", [[_hex(tx)]])
    assert verdict["allowed"] is True
