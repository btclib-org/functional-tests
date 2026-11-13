# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_balance`, on this harness: bitcoind and its own wallet.

Read from Core's `test/functional/wallet_balance.py`:
`wallet_balance_test.py` beside this module holds the body, run here
against bitcoind, with `BitcoindWalletFactory`'s wallets on it.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.bitcoind_wallet import BitcoindWalletFactory
from tests.integration.wallet_balance_test import balances_follow_the_wallet_s_coins

if TYPE_CHECKING:
    from collections.abc import Callable

    from btclib_functional_tests.bitcoind import BitcoindAdapter
    from btclib_functional_tests.capability import SkipCounts

pytestmark = pytest.mark.integration


def test_balances_follow_the_wallet_s_coins(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The oracle: the body this module's docstring names, over bitcoind."""
    balances_follow_the_wallet_s_coins(
        bitcoind_cluster, BitcoindWalletFactory(), skip_counts
    )
