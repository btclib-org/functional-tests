# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BitcoindWallet.spend` pays a script on the node's own chain: bitcoind.

No Core test is ported here. A `TxOut` built from a script's bytes, as
`Tx.parse` builds every output, carries btclib's default network,
mainnet, so its address on a regtest node is a `bc1` one, which Core
refuses. `spend` names each payee in the node's own chain instead.

    TF2_INTEGRATION=1 uv run pytest tests/integration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import TxOut
from btclib.tx.limits import COINBASE_MATURITY

from btclib_functional_tests.bitcoind_wallet import BitcoindWalletFactory
from btclib_functional_tests.capability import Capability, require

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.bitcoind import BitcoindAdapter
    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import WalletFactory

__all__ = ["spend_pays_a_script_given_as_bytes"]

pytestmark = pytest.mark.integration

_AMOUNT = 100_000_000


def spend_pays_a_script_given_as_bytes(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    wallets: WalletFactory,
    skip_counts: SkipCounts,
) -> None:
    """Pay a script given as bytes, and read it back at its own address.

    :param cluster: a node's own cluster fixture.
    :param wallets: the wallets under test.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    for capability in sorted(wallets.node_capabilities, key=lambda c: c.value):
        require(capability, node.capabilities, skip_counts)
    require(Capability.GENERATE, node.capabilities, skip_counts)

    node.restart(list(wallets.node_args))
    payer = wallets.create(node, "payer")
    payee = wallets.create(node, "payee")
    node.rpc.call("generatetoaddress", [COINBASE_MATURITY + 1, payer.new_address()])

    addr = payee.new_address()
    script = ScriptPubKey.from_address(addr).script
    assert TxOut(_AMOUNT, script).script_pub_key.address != addr
    tx = payer.spend([TxOut(_AMOUNT, script)])
    assert addr in {tx_out.script_pub_key.address for tx_out in tx.vout}
    node.rpc.call("generatetoaddress", [1, payer.new_address()])

    assert payee.balance(min_depth=1) == _AMOUNT
    (utxo,) = payee.utxos()
    assert utxo.tx_out.script_pub_key.address == addr
    assert addr.startswith("bcrt1")


def test_spend_pays_a_script_given_as_bytes_on_regtest(
    bitcoind_cluster: Callable[[int], list[BitcoindAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """A payee given as bytes is paid at its `bcrt1` address, and arrives."""
    spend_pays_a_script_given_as_bytes(
        bitcoind_cluster, BitcoindWalletFactory(), skip_counts
    )
