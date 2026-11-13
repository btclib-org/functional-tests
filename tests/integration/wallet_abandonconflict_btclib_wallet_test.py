# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_abandonconflict`: btclib-wallet over bitcoind.

`wallet_abandonconflict_test.py` beside this module holds the body, run
here with `BtclibWalletFactory`'s wallets on bitcoind nodes, so that a
failure is the wallet's rather than the node's.

    export TF2_INTEGRATION=1 TF2_BTCLIB_WALLET=1
    uv run pytest tests/integration/wallet_abandonconflict_btclib_wallet_test.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.wallet_abandonconflict_test import (
    abandon_frees_the_inputs_and_a_conflict_marks_both_sides,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from btclib_functional_tests.bitcoind import BitcoindAdapter
    from btclib_functional_tests.btclib_wallet_adapter import BtclibWalletFactory
    from btclib_functional_tests.capability import SkipCounts

pytestmark = pytest.mark.integration


def test_abandon_frees_the_inputs_and_a_conflict_marks_both_sides(
    btclib_wallets: BtclibWalletFactory,
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The wallet under test: the body this module's docstring names."""
    abandon_frees_the_inputs_and_a_conflict_marks_both_sides(
        bitcoind_cluster, btclib_wallets, skip_counts
    )
