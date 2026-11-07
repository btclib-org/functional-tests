# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_scantxoutset`, one body over each node.

Read from Core's `test/functional/rpc_scantxoutset.py` (`b388674acf06`,
2026-08-06): the node searches its own UTXO set for the outputs a list of
descriptors matches, over `scantxoutset` (`Capability.SCAN_UTXO_SET`),
and answers the amount they hold, the descriptors they match, and the
height, block and confirmations of one of them. `MiniWallet`
(`Capability.MINE`) pays each output searched for, and mines the block
holding them. Every assertion Core's file makes is kept, in its order,
the addresses, descriptors, amounts and messages copied from it.

Not every build refuses `start` with a null in place of its scan objects
the way the pinned file asserts, so the caller says which the running
node is expected to: the pinned file's missing argument, or, from a
release before `v32.0`, a null of the wrong type.

Core's first check differs in its number and not in what it asks: it
counts `49` coinbase outputs paying its `MiniWallet`, which is how many
the chain its framework mined before the test holds; this node starts at
height zero, so the count asked for is the number of blocks this
`MiniWallet` mined itself, each paying it one coinbase.

The keys Core's `getnewdestination` returns are random, and so are these,
btclib building each output and its address from one. So is the
descriptor Core's `MiniWallet.get_descriptor` returns for its own
output, `raw(<scriptPubKey>)` with its checksum, which btclib's
`descriptors.add_checksum` appends. The node is what searches for each.

Each `rpc_scantxoutset_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import secrets
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from bip32_descriptors.descriptors import descriptors
from bitcoin_core_rpc import RpcError
from btclib.key import PrvKeyData
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx.limits import COINBASE_MATURITY
from ecc.curves import secp256k1

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.mini_wallet import MiniWallet

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib.key import PubKeyData

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["the_utxo_set_is_searched_by_descriptor"]

_SATOSHI_PER_BTC = 100_000_000

# Core's own extended keys, one private and its own public
_TPRV = (
    "tprv8ZgxMBicQKsPd7Uf69XL1XwhmjHopUGep8GuEiJDZmbQz6o58LninorQAfcKZWARbtRtfnLc"
    "J5MQ2AtHcQJCCRUcMRvmDUjyEmNUWwx8UbK"
)
_TPUB = (
    "tpubD6NzVbkrYhZ4WaWSyoBvQwbpLkojyoTZPRsgXELWz3Popb3qkjcJyJUGLnL4qHHoQvao8ESa"
    "AstxYSnhyswJ76uZPStJRJCTKvosUCJZL5B"
)

# Core's own sends to children of `_TPRV`, each amount a power of two
# times 0.001 BTC, so that a sum of them names the subset it adds up
_HD_SENDS = (
    ("mkHV1C6JLheLoUSSZYk7x3FH5tnx9bu7yc", "0.008"),  # m/0'/0'/0'
    ("mipUSRmJAj2KrjSvsPQtnP8ynUon7FhpCR", "0.016"),  # m/0'/0'/1'
    ("n37dAGe6Mq1HGM9t4b6rFEEsDGq7Fcgfqg", "0.032"),  # m/0'/0'/1500'
    ("mqS9Rpg8nNLAzxFExsgFLCnzHBsoQ3PRM6", "0.064"),  # m/0'/0'/0
    ("mnTg5gVWr3rbhHaKjJv7EEEc76ZqHgSj4S", "0.128"),  # m/0'/0'/1
    ("mketCd6B9U9Uee1iCsppDJJBHfvi6U6ukC", "0.256"),  # m/0'/0'/1500
    ("mj8zFzrbBcdaWXowCQ1oPZ4qioBVzLzAp7", "0.512"),  # m/1/1/0'
    ("mfnKpKQEftniaoE1iXuMMePQU3PUpcNisA", "1.024"),  # m/1/1/1'
    ("mou6cB1kaP1nNJM1sryW6YRwnd4shTbXYQ", "2.048"),  # m/1/1/1500'
    ("mtfUoUax9L4tzXARpw1oTGxWyoogp52KhJ", "4.096"),  # m/1/1/0
    ("mxp7w7j8S1Aq6L8StS2PqVvtt4HGxXEvdy", "8.192"),  # m/1/1/1
    ("mpQ8rokAhp1TAtJQR6F6TaUmjAWkAWYYBq", "16.384"),  # m/1/1/1500
)
_LAST_HD_ADDRESS = _HD_SENDS[-1][0]

# Core's own "Test extended key derivation" scans, and the amount each finds
_DERIVATIONS: tuple[tuple[str | dict[str, object], str], ...] = (
    (f"combo({_TPRV}/0'/0h/0h)", "0.008"),
    (f"combo({_TPRV}/0'/0'/1h)", "0.016"),
    (f"combo({_TPRV}/0h/0'/1500')", "0.032"),
    (f"combo({_TPRV}/0h/0h/0)", "0.064"),
    (f"combo({_TPRV}/0'/0h/1)", "0.128"),
    (f"combo({_TPRV}/0h/0'/1500)", "0.256"),
    ({"desc": f"combo({_TPRV}/0'/0h/*h)", "range": 1499}, "0.024"),
    ({"desc": f"combo({_TPRV}/0'/0'/*h)", "range": 1500}, "0.056"),
    ({"desc": f"combo({_TPRV}/0h/0'/*)", "range": 1499}, "0.192"),
    ({"desc": f"combo({_TPRV}/0'/0h/*)", "range": 1500}, "0.448"),
    (f"combo({_TPRV}/1/1/0')", "0.512"),
    (f"combo({_TPRV}/1/1/1')", "1.024"),
    (f"combo({_TPRV}/1/1/1500h)", "2.048"),
    (f"combo({_TPRV}/1/1/0)", "4.096"),
    (f"combo({_TPRV}/1/1/1)", "8.192"),
    (f"combo({_TPRV}/1/1/1500)", "16.384"),
    (f"combo({_TPUB}/1/1/0)", "4.096"),
    (f"combo([abcdef88/1/2'/3/4h]{_TPUB}/1/1/1)", "8.192"),
    (f"combo({_TPUB}/1/1/1500)", "16.384"),
    ({"desc": f"combo({_TPRV}/1/1/*')", "range": 1499}, "1.536"),
    ({"desc": f"combo({_TPRV}/1/1/*')", "range": 1500}, "3.584"),
    ({"desc": f"combo({_TPRV}/1/1/*)", "range": 1499}, "12.288"),
    ({"desc": f"combo({_TPRV}/1/1/*)", "range": 1500}, "28.672"),
    ({"desc": f"combo({_TPUB}/1/1/*)", "range": 1499}, "12.288"),
    ({"desc": f"combo({_TPUB}/1/1/*)", "range": 1500}, "28.672"),
    ({"desc": f"combo({_TPUB}/1/1/*)", "range": [1500, 1500]}, "16.384"),
    ({"desc": f"pkh({_TPUB}/1/1/<0;1>)"}, "12.288"),
)

# Core's own "reported descriptors" scans, and the descriptors each reports
_REPORTED: tuple[tuple[str | dict[str, object], list[str]], ...] = (
    (
        {"desc": f"combo({_TPRV}/0h/0h/*)", "range": 1499},
        [
            (
                "pkh([0c5f9a1e/0h/0h/0]026dbd8b2315f296d36e6b6920b1579ca75569464875c7eb"
                "e869b536a7d9503c8c)#rthll0rg"
            ),
            (
                "pkh([0c5f9a1e/0h/0h/1]033e6f25d76c00bedb3a8993c7d5739ee806397f0529b1b3"
                "1dda31ef890f19a60c)#mcjajulr"
            ),
        ],
    ),
    (
        f"combo({_TPRV}/1/1/0)",
        [
            (
                "pkh([0c5f9a1e/1/1/0]03e1c5b6e650966971d7e71ef2674f80222752740fc1dfd63b"
                "bbd220d2da9bd0fb)#cxmct4w8"
            )
        ],
    ),
    (
        {"desc": f"combo({_TPUB}/1/1/*)", "range": 1500},
        [
            (
                "pkh([0c5f9a1e/1/1/0]03e1c5b6e650966971d7e71ef2674f80222752740fc1dfd63b"
                "bbd220d2da9bd0fb)#cxmct4w8"
            ),
            (
                "pkh([0c5f9a1e/1/1/1500]03832901c250025da2aebae2bfb38d5c703a57ab66ad477f"
                "9c578bfbcd78abca6f)#vchwd07g"
            ),
            (
                "pkh([0c5f9a1e/1/1/1]030d820fc9e8211c4169be8530efbc632775d8286167afd178"
                "caaf1089b77daba7)#z2t3ypsa"
            ),
        ],
    ),
)

# Core's own range refusals, `-8` each
_RANGE_REFUSALS: tuple[tuple[object, str], ...] = (
    (-1, "End of range is too high"),
    ([-1, 10], "Range should be greater or equal than 0"),
    ([(2 << 31 + 1) - 1000000, (2 << 31 + 1)], "End of range is too high"),
    ([2, 1], "Range specified as [begin,end] must not have begin after end"),
    ([0, 1000001], "Range is too large"),
)

# the refusal of `start` with a null scan-object list by a release before
# `v32.0`
_NULL_OF_THE_WRONG_TYPE = "JSON value of type null is not of expected type array"

# coins `MiniWallet` needs mature: one per output Core's own file sends to
_COINS = 3 + len(_HD_SENDS)

# `src/rpc/protocol.h`'s own codes for the refusals asserted below
_MISC_ERROR = -1
_TYPE_ERROR = -3
_INVALID_PARAMETER = -8


def _satoshi(btc: str) -> int:
    """Return a BTC amount, written as Core writes it, in satoshi."""
    return int(Decimal(btc) * _SATOSHI_PER_BTC)


def _scan(node: NodeAdapter, scanobjects: Sequence[object]) -> dict[str, Any]:
    """Return `scantxoutset`'s own answer to `start` over `scanobjects`."""
    answer = node.rpc.call("scantxoutset", ["start", list(scanobjects)])
    assert isinstance(answer, dict)
    return answer


def _total(node: NodeAdapter, scanobjects: Sequence[object]) -> Decimal:
    """Return the `total_amount` a `start` over `scanobjects` finds."""
    total = _scan(node, scanobjects)["total_amount"]
    assert isinstance(total, Decimal)
    return total


def _refused(node: NodeAdapter, params: list[object], code: int, message: str) -> None:
    """Assert `scantxoutset` refuses `params`, as Core's assertion asserts it.

    :param code: the node's own error code, `src/rpc/protocol.h`.
    :param message: a substring of the node's own error message.
    """
    with pytest.raises(RpcError) as refusal:
        node.rpc.call("scantxoutset", params)
    assert refusal.value.code == code, str(refusal.value)
    assert message in refusal.value.args[0], str(refusal.value)


def _new_destination(output_type: str) -> tuple[PubKeyData, ScriptPubKey]:
    """Return a random key and its output, Core's `getnewdestination`.

    :param output_type: `legacy`, `p2sh-segwit` or `bech32`.
    """
    pub = PrvKeyData(1 + secrets.randbelow(secp256k1.n - 1), network="regtest").pub
    if output_type == "legacy":
        return pub, ScriptPubKey.p2pkh(pub)
    if output_type == "p2sh-segwit":
        witness = ScriptPubKey.p2wpkh(pub)
        return pub, ScriptPubKey.p2sh(witness.script, "regtest")
    return pub, ScriptPubKey.p2wpkh(pub)


def the_utxo_set_is_searched_by_descriptor(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
    refuses_null_as_missing: Callable[[NodeAdapter], bool],
) -> None:
    """Core's `run_test`, in its order.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    :param refuses_null_as_missing: asked of the running node whether a
        null in place of `start`'s scan objects is refused as the missing
        argument, in the wording Core's pinned file asserts -- where not,
        as a release before `v32.0` refuses it, a null of the wrong type.
    """
    (node,) = cluster(1)
    require(Capability.SCAN_UTXO_SET, node.capabilities, skip_counts)
    require(Capability.MINE, node.capabilities, skip_counts)
    wallet = MiniWallet(node)
    mined = COINBASE_MATURITY + _COINS
    wallet.generate(mined)

    # every coinbase this wallet mined, found by its own descriptor
    wallet_descriptor = descriptors.add_checksum(
        f"raw({wallet.script_pub_key.script.hex()})"
    )
    unspents = _scan(node, [wallet_descriptor])["unspents"]
    assert sum(unspent["coinbase"] for unspent in unspents) == mined

    pub1, spk_p2sh_segwit = _new_destination("p2sh-segwit")
    pub2, spk_legacy = _new_destination("legacy")
    pub3, spk_bech32 = _new_destination("bech32")
    sent = [
        wallet.send_to(spk_p2sh_segwit, _satoshi("0.001")),
        wallet.send_to(spk_legacy, _satoshi("0.002")),
        wallet.send_to(spk_bech32, _satoshi("0.004")),
    ]
    sent.extend(
        wallet.send_to(ScriptPubKey.from_address(address), _satoshi(amount))
        for address, amount in _HD_SENDS
    )
    wallet.generate(1, confirm=sent)

    scan = _scan(node, [])
    info = node.rpc.call("gettxoutsetinfo")
    assert scan["success"] is True
    assert scan["height"] == info["height"]
    assert scan["txouts"] == info["txouts"]
    assert scan["bestblock"] == info["bestblock"]

    # the non-HD outputs, by key and by address
    keys = [pub.sec.hex() for pub in (pub1, pub2, pub3)]
    addresses = [spk.address for spk in (spk_p2sh_segwit, spk_legacy, spk_bech32)]
    assert _total(node, [f"pkh({key})" for key in keys]) == Decimal("0.002")
    assert _total(node, [f"wpkh({key})" for key in keys]) == Decimal("0.004")
    assert _total(node, [f"sh(wpkh({key}))" for key in keys]) == Decimal("0.001")
    assert _total(node, [f"combo({key})" for key in keys]) == Decimal("0.007")
    assert _total(node, [f"addr({address})" for address in addresses]) == Decimal(
        "0.007"
    )
    assert _total(
        node, [f"addr({addresses[0]})", f"addr({addresses[1]})", f"combo({keys[2]})"]
    ) == Decimal("0.007")

    # range validation
    for scan_range, message in _RANGE_REFUSALS:
        _refused(
            node,
            ["start", [{"desc": "desc", "range": scan_range}]],
            _INVALID_PARAMETER,
            message,
        )
    range_end = 2**31 - 1
    top_of_the_range = {
        "desc": f"combo({_TPRV}/0h/0'/*)",
        "range": [range_end, range_end],
    }
    assert _scan(node, [top_of_the_range])["success"] is True

    # extended key derivation, each sum naming the subset it adds up
    for scanobject, amount in _DERIVATIONS:
        assert _total(node, [scanobject]) == Decimal(amount), scanobject

    # the descriptors reported for a few matches
    for scanobject, reported in _REPORTED:
        answer = _scan(node, [scanobject])
        assert sorted(unspent["desc"] for unspent in answer["unspents"]) == reported

    # neither `status` nor `abort` needs a second argument
    assert node.rpc.call("scantxoutset", ["status"]) is None
    assert node.rpc.call("scantxoutset", ["abort"]) is False

    # the height, block and confirmations of a match
    wallet.generate(2)
    unspent = _scan(node, [f"addr({_LAST_HD_ADDRESS})"])["unspents"][0]
    assert unspent["height"] == info["height"]
    assert unspent["blockhash"] == node.rpc.call("getblockhash", [info["height"]])
    assert unspent["confirmations"] == 3

    # the action is needed, and so are the scan objects for `start`
    _refused(node, [], _MISC_ERROR, 'scantxoutset "action" ( [scanobjects,...] )')
    needed = "scanobjects argument is required for the start action"
    _refused(node, ["start"], _MISC_ERROR, needed)
    if refuses_null_as_missing(node):
        _refused(node, ["start", None], _MISC_ERROR, needed)
    else:
        _refused(node, ["start", None], _TYPE_ERROR, _NULL_OF_THE_WRONG_TYPE)
    _refused(
        node,
        ["invalid_command"],
        _INVALID_PARAMETER,
        "Invalid action 'invalid_command'",
    )
