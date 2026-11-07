# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_dumptxoutset`, rewritten on this repository's harness.

Read from Core's `test/functional/rpc_dumptxoutset.py`:
`rpc_dumptxoutset_test.py` beside this module is the body, run here
against bitcoind, which declares every capability it asks for. Whether
that body expects a dump at a height a fork also reaches, or an older
release's refusal of it, is read from the running build's own
`getnetworkinfo` `version`
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)),
`_ROLLS_BACK_PAST_A_FORK_VERSION` below having why.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.bitcoind import BitcoindAdapter
from tests.integration.rpc_dumptxoutset_test import (
    the_utxo_set_is_dumped,
)

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

pytestmark = pytest.mark.integration

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which `dumptxoutset` rolls
# back without invalidating a block, and so dumps at a height a fork also
# reaches: `master`'s own `31.99`, which it reported when
# `49d5e835a87a1475329176290f53ddd6f2c2d2ec` was merged (`378e17f703`,
# 2026-04-19), `v32.0rc1` being the first tag carrying it. A known limit:
# a `master` build from the version's move to `31.99` (`b97abdcdf1`,
# 2026-03-10) until that merge reports `319900` and refuses all the same,
# so this test fails against such a build
_ROLLS_BACK_PAST_A_FORK_VERSION = 319900


def _rolls_back_past_a_fork(node: NodeAdapter) -> bool:
    """Say whether the running build dumps where Core's pinned file asserts."""
    version = node.rpc.call("getnetworkinfo")["version"]
    assert isinstance(version, int)
    return version >= _ROLLS_BACK_PAST_A_FORK_VERSION


def test_the_utxo_set_is_dumped(
    make_adapter: AdapterFactory,
    bitcoind_path: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """The oracle: the UTXO set dumped and rolled back, over bitcoind."""
    the_utxo_set_is_dumped(
        make_adapter,
        BitcoindAdapter,
        bitcoind_path,
        tmp_path,
        skip_counts,
        _rolls_back_past_a_fork,
    )
