# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_createmultisig`, rewritten on tf2's own harness: bitcoind.

Read from Core's `test/functional/rpc_createmultisig.py` (`771200ca4362`,
2026-06-30), whose subject splits in two. Construction --
`createmultisig` turning `(nsigs, pubkeys, output_type)` into an
address, a redeemScript and a descriptor, and falling back to a legacy
address with a warning where a key is uncompressed -- needs no coin and
no node wallet, and is this module's own. Spending one is
`rpc_createmultisig_test.py`'s body beside this module, run here against
bitcoind, which declares every capability it asks for. Which of
`combinerawtransaction`'s refusals that body asserts is read from the
running build's own `getnetworkinfo` `version`
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)),
`_MERGEABILITY_VERSION` below having why.

`test_multisig_script_limit` is kept whole: its refusals need no
construction at all, and a script past `OP_16`'s own key count is built
from its items by `multisig_script` (`rpc_createmultisig_test.py`), which
`ScriptPubKey.p2ms` (`btclib.script.script_pub_key`) and btclib's
descriptor parser would each refuse
([ISS btclib-org/btclib#2348](https://github.com/btclib-org/btclib/issues/2348)).
So are its 16-of-20 spends, run by the body beside this module, and the
"correct encoding" check up to Core's own twenty keys.

Dropped: `test_sortedmulti_descriptors_bip67`, which reads its vectors
from Core's own `data/rpc_bip67.json`, a vendored fixture this
repository does not carry.

The construction tests have no `_btclib_node_test.py` counterpart:
`createmultisig` is not in `btclib_node`'s own dispatch table
(`src/btclib_node/rpc/callbacks.py`, measured at the released
`2026.9.24`, `422d2640`, and at `main`, `4e155386`, alike), so a
btclib-node run of them would answer `Method not found` and nothing
more.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

import itertools
import secrets
from typing import TYPE_CHECKING

import pytest
from bip32_descriptors.descriptors import descriptors
from bitcoin_core_rpc import RpcError
from btclib.key import PrvKeyData
from btclib.script.script_pub_key import ScriptPubKey
from ecc.curves import secp256k1

from tests.integration.rpc_createmultisig_test import (
    M_OF_N,
    OUTPUT_TYPES,
    combinerawtransaction_preconditions,
    every_multisig_spends_once_the_node_combines_its_signatures,
    multisig_script,
    multisig_script_pub_key,
    sixteen_of_twenty_spends_once_the_node_combines_its_signatures,
    wrapped,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from btclib_functional_tests.bitcoind import BitcoindAdapter
    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

pytestmark = pytest.mark.integration

# deterministic, and never spent: construction is what these keys are
# for, so a scalar in 1..n-1 is as good a key as a random one --
# `PrvKeyData(i, network="regtest").pub` is the SEC bytes `createmultisig`
# and btclib's own descriptor parser both take.
# Core's own file makes one key past the most `createmultisig` takes.
_KEYS = [PrvKeyData(i, network="regtest").pub.sec.hex() for i in range(1, 22)]

# `MAX_PUBKEYS_PER_MULTISIG` (`src/script/script.h`), the most keys
# `createmultisig` takes
_MAX_PUBKEYS_PER_MULTISIG = 20

# the most keys `ScriptPubKey.p2ms` takes, `OP_16`'s own count
_OP_16 = 16

# `RPC_INVALID_PARAMETER` (`src/rpc/protocol.h`)
_INVALID_PARAMETER = -8

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which `combinerawtransaction`
# refuses a single transaction and one differing from the first: `v32.0`'s,
# `v32.0rc1` being the first tag carrying
# `6d86184a8bcc34186d217cc7cfbf7acc21af7ca4`. A known limit: a `master`
# build from its merge (`9961229360`, 2026-05-13) until the version moved
# to `32.99` (`f3fec67c3e`, 2026-09-11) reports `319900` and refuses them
# all the same, so this test fails against such a build
_MERGEABILITY_VERSION = 320000

_UNCOMPRESSED_WARNING = (
    "Unable to make chosen address type, please ensure no uncompressed "
    "public keys are present."
)


def test_address_redeemscript_and_descriptor_match_btclibs_own_construction(
    bitcoind_adapter: BitcoindAdapter,
) -> None:
    """Every `(nsigs, nkeys, output_type)` Core's own file names, matched."""
    for nsigs, nkeys in M_OF_N:
        keys = _KEYS[:nkeys]
        # a bare `multi()` takes at most three keys, so it sits in a `wsh()`
        wsh = descriptors.parse(
            f"wsh(multi({nsigs},{','.join(keys)}))", network="regtest"
        )
        assert isinstance(wsh, descriptors.WshDescriptor)
        expected_redeem_script = wsh.inner.redeem_script()
        for output_type in OUTPUT_TYPES:
            result = bitcoind_adapter.rpc.call(
                "createmultisig", [nsigs, keys, output_type]
            )
            descriptor = descriptors.parse(
                wrapped(nsigs, keys, output_type), network="regtest"
            )
            assert "warnings" not in result
            assert result["redeemScript"] == expected_redeem_script.hex()
            assert result["address"] == descriptor.address()
            assert result["descriptor"] == descriptors.add_checksum(str(descriptor))


def test_encodes_every_key_count_createmultisig_takes(
    bitcoind_adapter: BitcoindAdapter,
) -> None:
    """`n`-of-`n`, for every `n` up to `MAX_PUBKEYS_PER_MULTISIG`.

    Up to `OP_16`'s own count, `multisig_script` is also
    `ScriptPubKey.p2ms`'s own script.
    """
    for nkeys in range(1, _MAX_PUBKEYS_PER_MULTISIG + 1):
        keys = [_KEYS[0]] * nkeys
        expected = multisig_script(nkeys, keys)
        if nkeys <= _OP_16:
            bare = ScriptPubKey.p2ms(
                nkeys,
                [PrvKeyData(1, network="regtest").pub] * nkeys,
                lexicographic_sorting=False,
            )
            assert bare.script == expected
        result = bitcoind_adapter.rpc.call("createmultisig", [nkeys, keys, "bech32"])
        assert result["redeemScript"] == expected.hex()


def test_sixteen_of_twenty_matches_the_script_level_construction(
    bitcoind_adapter: BitcoindAdapter,
) -> None:
    """`test_multisig_script_limit`'s own 16-of-20, `p2sh-segwit` and `bech32`.

    The address and redeemScript are `multisig_script`'s and
    `multisig_script_pub_key`'s own, and the descriptor is Core's own
    string with its checksum, as `do_multisig` builds it.
    """
    keys = _KEYS[:_MAX_PUBKEYS_PER_MULTISIG]
    redeem_script = multisig_script(16, keys)
    for output_type in ("p2sh-segwit", "bech32"):
        result = bitcoind_adapter.rpc.call("createmultisig", [16, keys, output_type])
        assert "warnings" not in result
        assert result["redeemScript"] == redeem_script.hex()
        expected = multisig_script_pub_key(redeem_script, output_type)
        assert result["address"] == expected.address
        assert result["descriptor"] == descriptors.add_checksum(
            wrapped(16, keys, output_type)
        )


def test_multisig_script_limit_refusals(bitcoind_adapter: BitcoindAdapter) -> None:
    """Too large a legacy redeemScript, and too many keys, are refused."""
    refusals = [
        ([16, _KEYS[:20], "legacy"], "redeemScript exceeds size limit: 684 > 520"),
        (
            [16, _KEYS, "p2sh-segwit"],
            "Number of keys involved in the multisignature address creation > 20",
        ),
        (
            [16, _KEYS, "bech32"],
            "Number of keys involved in the multisignature address creation > 20",
        ),
    ]
    for params, message in refusals:
        with pytest.raises(RpcError) as refusal:
            bitcoind_adapter.rpc.call("createmultisig", params)
        assert refusal.value.code == _INVALID_PARAMETER
        assert message in refusal.value.args[0]


def test_refuses_bech32m(bitcoind_adapter: BitcoindAdapter) -> None:
    """`bech32m` is refused: `createmultisig` has no witness v1 output."""
    with pytest.raises(
        RpcError, match="createmultisig cannot create bech32m multisig addresses"
    ):
        bitcoind_adapter.rpc.call("createmultisig", [2, _KEYS[:3], "bech32m"])


def test_mixing_uncompressed_and_compressed_keys(
    bitcoind_adapter: BitcoindAdapter,
) -> None:
    """An uncompressed key makes every output type a legacy one, with a warning.

    Core's `test_mixing_uncompressed_and_compressed_keys`, over every
    order of two compressed random keys and one uncompressed one.
    """
    scalars = [1 + secrets.randbelow(secp256k1.n - 1) for _ in range(3)]
    pk0, pk1 = (PrvKeyData(q, network="regtest").pub.sec.hex() for q in scalars[:2])
    pk2 = PrvKeyData(scalars[2], network="regtest", compressed=False).pub.sec.hex()
    for keys in itertools.permutations([pk0, pk1, pk2]):
        legacy = bitcoind_adapter.rpc.call("createmultisig", [2, list(keys), "legacy"])
        expected = descriptors.parse(wrapped(2, keys, "legacy"), network="regtest")
        assert legacy["address"] == expected.address()
        for output_type in ("bech32", "p2sh-segwit"):
            result = bitcoind_adapter.rpc.call(
                "createmultisig", [2, list(keys), output_type]
            )
            assert result["address"] == legacy["address"]
            assert result["warnings"] == [_UNCOMPRESSED_WARNING]


def test_every_multisig_spends_once_the_node_combines_its_signatures(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The oracle: the body this module's docstring names, over bitcoind."""
    every_multisig_spends_once_the_node_combines_its_signatures(
        bitcoind_cluster, skip_counts
    )


def test_sixteen_of_twenty_spends_once_the_node_combines_its_signatures(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The oracle: the body this module's docstring names, over bitcoind."""
    sixteen_of_twenty_spends_once_the_node_combines_its_signatures(
        bitcoind_cluster, skip_counts
    )


def test_combinerawtransaction_preconditions(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The oracle: the body this module's docstring names, over bitcoind."""
    combinerawtransaction_preconditions(
        bitcoind_cluster, skip_counts, _checks_mergeability
    )


def _checks_mergeability(node: NodeAdapter) -> bool:
    """Say whether the running build refuses what Core's pinned file asserts."""
    version = node.rpc.call("getnetworkinfo")["version"]
    assert isinstance(version, int)
    return version >= _MERGEABILITY_VERSION
