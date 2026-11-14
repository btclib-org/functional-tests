# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_txn_doublespend`: btclib-wallet over bitcoind.

`wallet_txn_doublespend_test.py` beside this module holds the body, run
here with `BtclibWalletFactory`'s wallets on bitcoind nodes, so that a
failure is the wallet's rather than the node's.

    export TF2_INTEGRATION=1 TF2_BTCLIB_WALLET=1
    uv run pytest tests/integration/wallet_txn_doublespend_btclib_wallet_test.py
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.wallet_txn_doublespend_test import (
    a_double_spend_conflicts_the_wallet_s_payments,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from btclib_functional_tests.bitcoind import BitcoindAdapter
    from btclib_functional_tests.btclib_wallet_adapter import BtclibWalletFactory
    from btclib_functional_tests.capability import SkipCounts

pytestmark = pytest.mark.integration


def test_a_double_spend_conflicts_the_wallet_s_payments(
    btclib_wallets: BtclibWalletFactory,
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The wallet under test, without `--mineblock`."""
    a_double_spend_conflicts_the_wallet_s_payments(
        bitcoind_cluster, btclib_wallets, skip_counts, mine_block=False
    )


def test_a_double_spend_conflicts_the_wallet_s_confirmed_payments(
    btclib_wallets: BtclibWalletFactory,
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The wallet under test, with Core's `--mineblock`."""
    a_double_spend_conflicts_the_wallet_s_payments(
        bitcoind_cluster, btclib_wallets, skip_counts, mine_block=True
    )
