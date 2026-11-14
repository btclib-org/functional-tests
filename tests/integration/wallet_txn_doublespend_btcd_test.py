# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_txn_doublespend`, on tf2's own harness: btcd.

`wallet_txn_doublespend_test.py` beside this module is the body, run
here against btcd, a target rather than the oracle (rule 3 of issue
btclib-org/btclib#2220). Its only wallets are `BitcoindWalletFactory`'s,
which need `Capability.NODE_WALLET`, which btcd does not declare.

    export TF2_INTEGRATION=1 TF2_BTCD=<btcd>
    uv run pytest tests/integration/wallet_txn_doublespend_btcd_test.py
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

    from btclib_functional_tests.btcd import BtcdAdapter
    from btclib_functional_tests.capability import SkipCounts

pytestmark = pytest.mark.integration


def test_a_double_spend_conflicts_the_wallet_s_payments(
    btcd_cluster: Callable[[int], list[BtcdAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The target, over btcd, without `--mineblock`."""
    a_double_spend_conflicts_the_wallet_s_payments(
        btcd_cluster, BitcoindWalletFactory(), skip_counts, mine_block=False
    )


def test_a_double_spend_conflicts_the_wallet_s_confirmed_payments(
    btcd_cluster: Callable[[int], list[BtcdAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """The target, over btcd, with Core's `--mineblock`."""
    a_double_spend_conflicts_the_wallet_s_payments(
        btcd_cluster, BitcoindWalletFactory(), skip_counts, mine_block=True
    )
