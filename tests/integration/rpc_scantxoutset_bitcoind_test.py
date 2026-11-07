# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_scantxoutset`, rewritten on tf2's own harness: bitcoind.

Read from Core's `test/functional/rpc_scantxoutset.py`:
`rpc_scantxoutset_test.py` beside this module is the body, run here
against bitcoind, which declares every capability it asks for. Whether
that body expects a null in place of `start`'s scan objects refused as
Core's pinned file refuses it is read from the running build's own
`getnetworkinfo` `version`
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)),
`_NULL_AS_MISSING_VERSION` below having why.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.rpc_scantxoutset_test import (
    the_utxo_set_is_searched_by_descriptor,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from btclib_functional_tests.bitcoind import BitcoindAdapter
    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

pytestmark = pytest.mark.integration

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which `scantxoutset` refuses
# `start` with a null scan-object list as the missing argument: `v32.0`'s,
# `v32.0rc1` being the first tag carrying
# `aeca0610865ede44004b42a16ef6318245fe0644`. A known limit: a `master`
# build from its merge (`3db96eb5fd`, 2026-08-05) until the version moved
# to `32.99` (`f3fec67c3e`, 2026-09-11) reports `319900` and refuses it
# that way all the same, so this test fails against such a build
_NULL_AS_MISSING_VERSION = 320000


def _refuses_null_as_missing(node: NodeAdapter) -> bool:
    """Say whether the running build refuses what Core's pinned file asserts."""
    version = node.rpc.call("getnetworkinfo")["version"]
    assert isinstance(version, int)
    return version >= _NULL_AS_MISSING_VERSION


def test_the_utxo_set_is_searched_by_descriptor(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The oracle: the body this module's docstring names, over bitcoind."""
    the_utxo_set_is_searched_by_descriptor(
        bitcoind_cluster, skip_counts, _refuses_null_as_missing
    )
