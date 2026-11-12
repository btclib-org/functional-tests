# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `wallet_miniscript_decaying_multisig_descriptor_psbt`, one body.

Read from Core's
`test/functional/wallet_miniscript_decaying_multisig_descriptor_psbt.py`
(`88f802983571`, 2026-01-31, the same file at `v32.1`): a watch-only
wallet imports a miniscript `thresh` over four signers' keys and three
block-height locks, so that it spends 4-of-4 before the first lock, then
3-of-4, 2-of-4 and 1-of-4 as each lock height is reached. Each spend is
a PSBT the signers complete one after another, in a random order; a
spend signed by fewer signers than all four is refused as `non-final`
before its lock height and accepted once the chain reaches it. Every
assertion of Core's own is kept.

`Capability.NODE_WALLET` (`capability.py`) is asked for first, then
`Capability.GENERATE`, the `generatetoaddress` the body mines with, of a
fresh node of the body's own on a clean chain, as Core's
`setup_clean_chain` asks, which it restarts under `-keypool=100`: Core's
`extra_args` start the node with the option, and `bitcoind_cluster`
(`tests/integration/conftest.py`) starts one with none. Core's
`wallet_names` is empty, so its harness creates no wallet, and Core's
`generate` mines to its deterministic key's address, `PRIV_KEYS[0]`
(`test_node.py`); this mines to that same address, which no wallet of
the node holds. The lock heights are Core's own, so the body mines
exactly the blocks Core's file does and no other. Core's
`assert_approx` is `approx` (`wallet_simulaterawtx_test.py`).

Each
`wallet_miniscript_decaying_multisig_descriptor_psbt_<node>_test.py`
module beside this one runs it, `tests/integration/conftest.py`'s own
module docstring having how.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import TYPE_CHECKING

from btclib_functional_tests.capability import Capability, require
from tests.integration.wallet_signmessagewithaddress_test import refused
from tests.integration.wallet_simulaterawtx_test import approx

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from bitcoin_core_rpc import BitcoinCoreRpcClient

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["a_decaying_multisig_spends_with_fewer_signers_past_each_lock"]

# Core's `TestNode.PRIV_KEYS[0]` (`test_node.py`): the address Core's
# `generate` pays for node 0
_DETERMINISTIC_ADDRESS = "mjTkW3DjgyZck4KbiRusZsqTgaYTxdSz6z"

# `src/rpc/protocol.h`: what `sendrawtransaction` answers a transaction
# the mempool refuses
_RPC_VERIFY_REJECTED = -26

_VSPAN = Decimal("0.001")


def _get_xpub(wallet: BitcoinCoreRpcClient) -> str:
    """Return the key of the wallet's receiving `pkh` descriptor, multipath.

    Core's own `_get_xpub`: the `pkh` descriptor, being the least likely
    to be reused by accident, with its key origin kept and its receiving
    index replaced by the `<0;1>` multipath convention.
    """
    pkh_descriptor = next(
        d
        for d in wallet.call("listdescriptors")["descriptors"]
        if d["desc"].startswith("pkh(") and not d["internal"]
    )
    desc = pkh_descriptor["desc"]
    assert isinstance(desc, str)
    return desc.split("pkh(")[1].split(")")[0].replace("/0/*", "/<0;1>/*")


def a_decaying_multisig_spends_with_fewer_signers_past_each_lock(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's `run_test`, in its own order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.NODE_WALLET, node.capabilities, skip_counts)
    require(Capability.GENERATE, node.capabilities, skip_counts)
    node.restart(["-keypool=100"])
    m = 4  # starts as 4-of-4
    n = 4

    locktimes = [104, 106, 108]
    assert len(locktimes) == n - 1

    name = f"{m}_of_{n}_decaying_multisig"

    # the signer wallets, and their xpubs
    signers = [
        node.rpc.for_wallet(
            node.rpc.call("createwallet", {"wallet_name": f"signer_{i}"})["name"]
        )
        for i in range(n)
    ]
    xpubs = [_get_xpub(signer) for signer in signers]

    # the watch-only decaying multisig, from the signers' xpubs: Core's
    # own `create_multisig`
    node.rpc.call(
        "createwallet",
        {"wallet_name": name, "blank": True, "disable_private_keys": True},
    )
    multisig = node.rpc.for_wallet(name)
    # spending policy: `thresh(4,pk(key_1),pk(key_2),pk(key_3),pk(key_4),
    # after(t1),after(t2),after(t3))`
    multisig_desc = (
        f"wsh(thresh({n},pk({'),s:pk('.join(xpubs)}),"
        f"sln:after({'),sln:after('.join(map(str, locktimes))})))"
    )
    checksum = multisig.call("getdescriptorinfo", [multisig_desc])["checksum"]
    result = multisig.call(
        "importdescriptors",
        [
            [
                {  # multipath descriptor expands to receive and change
                    "desc": f"{multisig_desc}#{checksum}",
                    "active": True,
                    "timestamp": "now",
                },
            ]
        ],
    )
    assert all(r["success"] for r in result)

    # a mature utxo to send to the multisig
    coordinator_wallet = node.rpc.for_wallet(
        node.rpc.call("createwallet", {"wallet_name": "coordinator"})["name"]
    )
    node.rpc.call("generatetoaddress", [101, coordinator_wallet.call("getnewaddress")])

    # funds sent to the multisig's receiving address
    deposit_amount = 6.15
    coordinator_wallet.call(
        "sendtoaddress", [multisig.call("getnewaddress"), deposit_amount]
    )
    node.rpc.call("generatetoaddress", [1, _DETERMINISTIC_ADDRESS])
    approx(multisig.call("getbalance"), Decimal(deposit_amount), _VSPAN)

    # transactions sent from the multisig as the required signers decay
    amount = 1.5
    receiver = signers[0]
    sent = 0.0
    for locktime in [0, *locktimes]:
        current_height = node.rpc.call("getblock", [node.rpc.call("getbestblockhash")])[
            "height"
        ]

        # each signer signs the same psbt "in series", one after the other
        psbt = multisig.call(
            "walletcreatefundedpsbt",
            {
                "inputs": [],
                "outputs": {receiver.call("getnewaddress"): amount},
                "feeRate": 0.00010,
                "locktime": locktime,
            },
        )
        # the random sample asserts that any of the signing keys can sign
        # for the 3-of-4, 2-of-4 and 1-of-4
        for i, signer in enumerate(random.sample(range(m), m)):
            psbt = signers[signer].call("walletprocesspsbt", [psbt["psbt"]])
            assert psbt["complete"] == (i == m - 1)

        if m < n:
            # the time-locked transaction is too immature to spend with
            # m-of-n at this height
            assert current_height < locktime
            refused(
                multisig,
                "sendrawtransaction",
                [psbt["hex"]],
                _RPC_VERIFY_REJECTED,
                "non-final",
            )

            # blocks up to the time-lock height, then the broadcast
            node.rpc.call(
                "generatetoaddress", [locktime - current_height, _DETERMINISTIC_ADDRESS]
            )
        # otherwise every signer is required to spend before the first lock

        multisig.call("sendrawtransaction", [psbt["hex"]])
        sent += amount

        # balances once the transaction is in a block
        node.rpc.call("generatetoaddress", [1, _DETERMINISTIC_ADDRESS])
        approx(multisig.call("getbalance"), Decimal(deposit_amount - sent), _VSPAN)
        assert receiver.call("getbalance") == Decimal(sent)

        m -= 1  # the number of required signers decays for the next lock
