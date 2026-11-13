# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_abandonconflict`, on tf2's own harness: btcd.

`wallet_abandonconflict_test.py` beside this module is the body, run
here against btcd, a target rather than the oracle (rule 3 of issue
btclib-org/btclib#2220). Its only wallets are `BitcoindWalletFactory`'s,
which need `Capability.NODE_WALLET`, which btcd does not declare.

    export TF2_INTEGRATION=1 TF2_BTCD=<btcd>
    uv run pytest tests/integration/wallet_abandonconflict_btcd_test.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.bitcoind_wallet import BitcoindWalletFactory
from tests.integration.wallet_abandonconflict_test import (
    abandon_frees_the_inputs_and_a_conflict_marks_both_sides,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from btclib_functional_tests.btcd import BtcdAdapter
    from btclib_functional_tests.capability import SkipCounts

pytestmark = pytest.mark.integration


def test_abandon_frees_the_inputs_and_a_conflict_marks_both_sides(
    btcd_cluster: Callable[[int], list[BtcdAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The target: the body this module's docstring names, over btcd."""
    abandon_frees_the_inputs_and_a_conflict_marks_both_sides(
        btcd_cluster, BitcoindWalletFactory(), skip_counts
    )
