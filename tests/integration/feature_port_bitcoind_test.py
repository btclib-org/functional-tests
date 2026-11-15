# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_port`, rewritten on this repository's harness.

Read from Core's `test/functional/feature_port.py`:
`feature_port_test.py` beside this module is the body, run here against
bitcoind, which declares every capability it asks for.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.integration.support.bitcoind_adapters import UnboundBitcoindAdapter
from tests.integration.feature_port_test import (
    port_and_bind_decide_where_the_node_listens,
)

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from tests.conftest import AdapterFactory

pytestmark = pytest.mark.integration


def test_port_and_bind_decide_where_the_node_listens(
    make_adapter: AdapterFactory,
    bitcoind_path: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """The oracle: where `-port` and `-bind` have bitcoind listen."""
    port_and_bind_decide_where_the_node_listens(
        make_adapter, UnboundBitcoindAdapter, bitcoind_path, tmp_path, skip_counts
    )
