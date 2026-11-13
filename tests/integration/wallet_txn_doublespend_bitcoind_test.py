# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_txn_doublespend`, on this harness: bitcoind and its wallet.

Read from Core's `test/functional/wallet_txn_doublespend.py`:
`wallet_txn_doublespend_test.py` beside this module holds the body, run
here against bitcoind, with `BitcoindWalletFactory`'s wallets on it.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.bitcoind_wallet import BitcoindWalletFactory
from tests.integration.wallet_txn_doublespend_test import (
    a_double_spend_conflicts_the_wallet_s_payments,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from btclib_functional_tests.bitcoind import BitcoindAdapter
    from btclib_functional_tests.capability import SkipCounts

pytestmark = pytest.mark.integration


def test_a_double_spend_conflicts_the_wallet_s_payments(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The oracle, over bitcoind, without `--mineblock`."""
    a_double_spend_conflicts_the_wallet_s_payments(
        bitcoind_cluster, BitcoindWalletFactory(), skip_counts, mine_block=False
    )


def test_a_double_spend_conflicts_the_wallet_s_confirmed_payments(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The oracle, over bitcoind, with Core's `--mineblock`."""
    a_double_spend_conflicts_the_wallet_s_payments(
        bitcoind_cluster, BitcoindWalletFactory(), skip_counts, mine_block=True
    )
