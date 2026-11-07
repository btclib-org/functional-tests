# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_disable`, rewritten on tf2's own harness: bitcoind.

Read from Core's `test/functional/wallet_disable.py` (`3fd68a95e68b`,
2026-04-07, the same file at `v32.1`; `v30.3` differs only in asserting
with `assert` where this revision calls `assert_equal`): a node started
under `-disablewallet` answers no wallet RPC and still validates an
address. Every assertion of Core's own is kept.

A bitcoind-only test in `capability.py`'s own sense: `-disablewallet` is
bitcoind's own option, so this module asks `require` for nothing and has
no `_btclib_node_test.py` counterpart. It asks for no
`Capability.NODE_WALLET` either, as Core's file does not skip where the
build carries no wallet: such a build still accepts `-disablewallet`,
among the wallet options `src/dummywallet.cpp` registers for it, and
answers no wallet RPC regardless.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.node import free_ports
from tests.integration.wallet_signmessagewithaddress_test import refused

if TYPE_CHECKING:
    from pathlib import Path

    from tests.conftest import AdapterFactory

pytestmark = pytest.mark.integration

# `src/rpc/protocol.h`: what a method the node does not register answers
_RPC_METHOD_NOT_FOUND = -32601


def test_disablewallet_leaves_no_wallet_rpc_and_validates_an_address(
    make_adapter: AdapterFactory, bitcoind_path: str, tmp_path: Path
) -> None:
    """Core's `run_test`, on a clean-chain node started under the option."""
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        BitcoindAdapter,
        bitcoind_path,
        tmp_path,
        rpc_port,
        p2p_port,
        extra_args=("-disablewallet",),
    )
    adapter.start()
    try:
        # the wallet is really disabled
        refused(
            adapter.rpc,
            "getwalletinfo",
            [],
            _RPC_METHOD_NOT_FOUND,
            "Method not found",
        )
        # a mainnet address is not valid on regtest, a testnet one is
        x = adapter.rpc.call("validateaddress", ["3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy"])
        assert x["isvalid"] is False
        x = adapter.rpc.call("validateaddress", ["mneYUmWYsuk7kySiURxCi3AGxrAqZxLgPZ"])
        assert x["isvalid"] is True
    finally:
        adapter.stop()
