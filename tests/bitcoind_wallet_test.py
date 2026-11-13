# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BitcoindWallet`, driven against a faked node rather than a spawned one.

Each method's own RPC call and the translation of its answer: what a run
of `tests/integration/wallet_balance_bitcoind_test.py` does against a
real bitcoind, without starting one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import TYPE_CHECKING, cast

import pytest
from bitcoin_core_rpc import BitcoinCoreRpcClient, RpcError
from btclib.script import ScriptPubKey
from btclib.script.script_pub_key import address
from btclib.tx import OutPoint, Tx, TxIn, TxOut

from btclib_functional_tests.bitcoind_wallet import (
    BitcoindWallet,
    BitcoindWalletFactory,
)
from btclib_functional_tests.capability import (
    Capability,
    MissingCapabilityError,
    SkipCounts,
)
from btclib_functional_tests.wallet import (
    Balances,
    BlockRef,
    TxInfo,
    Utxo,
    WalletCapability,
)
from tests.integration.bitcoind_wallet_spend_bitcoind_test import (
    spend_pays_a_script_given_as_bytes,
)

if TYPE_CHECKING:
    from urllib.request import Request

    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import WalletAdapter, WalletFactory

_NAME = "w"

_SYNC = ("", "syncwithvalidationinterfacequeue", None)

_SCRIPT = b"\x00\x14" + bytes(20)

_TXID = "11" * 32


def _tx(value: int = 1_000, network: str = "regtest") -> Tx:
    """Return a one-input, one-output transaction, unsigned."""
    vin = [TxIn(OutPoint(bytes.fromhex(_TXID), 0), sequence=0xFFFFFFFD)]
    return Tx(2, 0, vin, [TxOut(value, ScriptPubKey(_SCRIPT, network))])


# a regtest WIF, standing for any key a wallet's answer could quote
_WIF = "cMahea7zqjxrtgAbB7LSGbcQUr1uX1ojuat9jZodMN9wHZo77nNv"


def _assert_no_key(exc: BaseException) -> None:
    """Assert `_WIF` is in no text or argument of `exc`, which chains nothing.

    `__context__` is asked as well as `__cause__`: `raise ... from None`
    hides the first from a traceback and keeps it.
    """
    assert _WIF not in str(exc)
    assert all(_WIF not in repr(arg) for arg in exc.args)
    assert all(_WIF not in repr(value) for value in vars(exc).values())
    assert exc.__cause__ is None
    assert exc.__context__ is None


def _hex(tx: Tx) -> str:
    return tx.serialize(include_witness=True).hex()


type Params = Sequence[object] | Mapping[str, object] | None


class _FakeRpc:
    """A `BitcoinCoreRpcClient` stand-in at one endpoint: `""` or a wallet's.

    Every client shares one record of calls and one table of answers, as
    every client of one adapter shares its node.
    """

    def __init__(
        self,
        answers: dict[str, object],
        calls: list[tuple[str, str, Params]],
        endpoint: str = "",
    ) -> None:
        self._answers = answers
        self.calls = calls
        self._endpoint = endpoint

    def call(self, method: str, params: Params = None) -> object:
        self.calls.append((self._endpoint, method, params))
        return self._answers.get(method)

    def for_wallet(self, wallet_name: str) -> _FakeRpc:
        return _FakeRpc(self._answers, self.calls, wallet_name)


class _FakeNode:
    """What `BitcoindWallet` reads of a node: its `rpc` and its `chain`."""

    def __init__(self, answers: dict[str, object], chain: str = "regtest") -> None:
        self.calls: list[tuple[str, str, Params]] = []
        self._rpc = _FakeRpc(answers, self.calls)
        self._chain = chain

    @property
    def rpc(self) -> _FakeRpc:
        return self._rpc

    @property
    def chain(self) -> str:
        return self._chain


def _wallet(
    chain: str = "regtest", **answers: object
) -> tuple[BitcoindWallet, _FakeNode]:
    node = _FakeNode(dict(answers), chain)
    return BitcoindWallet(cast("NodeAdapter", node), _NAME), node


def test_the_wallet_and_its_factory_are_the_protocol_s() -> None:
    """Both satisfy the protocols a body is written against."""
    node = _FakeNode({})
    factory: WalletFactory = BitcoindWalletFactory()
    wallet: WalletAdapter = factory.create(cast("NodeAdapter", node), _NAME)
    assert wallet.name == _NAME
    assert wallet.node is cast("NodeAdapter", node)
    assert node.calls == [
        ("", "createwallet", {"wallet_name": _NAME, "load_on_startup": True})
    ]


def test_the_factory_declares_the_node_s_wallet_and_every_capability() -> None:
    """The wallet is its node's own, and has every wallet capability."""
    factory = BitcoindWalletFactory()
    assert factory.node_capabilities == {Capability.NODE_WALLET}
    assert factory.capabilities == set(WalletCapability)
    assert factory.node_args == ("-walletrejectlongchains=0",)


def test_each_read_delivers_the_node_s_queue_first() -> None:
    """The node's `syncwithvalidationinterfacequeue`, then the wallet's call."""
    wallet, node = _wallet(getnewaddress="bcrt1qa", getrawchangeaddress="bcrt1qb")
    assert wallet.new_address() == "bcrt1qa"
    assert wallet.new_change_address() == "bcrt1qb"
    assert node.calls == [
        _SYNC,
        (_NAME, "getnewaddress", None),
        _SYNC,
        (_NAME, "getrawchangeaddress", None),
    ]


def test_balance_is_getbalance_in_satoshi() -> None:
    """`min_depth` is `minconf`, and the answer is read in satoshi."""
    wallet, node = _wallet(getbalance=Decimal("9.99000000"))
    assert wallet.balance(1) == 999_000_000
    assert node.calls[-1] == (_NAME, "getbalance", {"minconf": 1})


def test_a_balance_that_is_no_amount_is_refused() -> None:
    """A float, or anything but a `Decimal`, is not read as an amount."""
    wallet, _ = _wallet(getbalance=9.99)
    with pytest.raises(TypeError, match="not an amount: a float$"):
        wallet.balance()


def test_balances_are_getbalances_mine() -> None:
    """Each bucket of `mine`, in satoshi; `lastprocessedblock` is not one."""
    mine = {
        "trusted": Decimal(1),
        "untrusted_pending": Decimal("0.5"),
        "immature": Decimal("0E-8"),
        "nonmempool": Decimal("0.00000001"),
    }
    wallet, _ = _wallet(getbalances={"mine": mine, "lastprocessedblock": {}})
    assert wallet.balances() == Balances(100_000_000, 50_000_000, 0, 1)


def test_balances_of_a_build_without_nonmempool_leave_it_none() -> None:
    """A bitcoind before `v32.0` answers no `nonmempool`."""
    mine = {
        "trusted": Decimal(1),
        "untrusted_pending": Decimal(0),
        "immature": Decimal(0),
    }
    wallet, _ = _wallet(getbalances={"mine": mine})
    assert wallet.balances() == Balances(100_000_000, 0, 0, None)


def test_an_answer_that_is_no_object_is_refused() -> None:
    """`getbalances` answering a list is named in the error."""
    wallet, _ = _wallet(getbalances=[])
    with pytest.raises(TypeError, match="getbalances answered"):
        wallet.balances()


def test_utxos_are_the_spendable_entries_of_listunspent() -> None:
    """An entry the wallet cannot spend is left out; the rest are read."""
    entry = {
        "txid": _TXID,
        "vout": 1,
        "amount": Decimal("0.5"),
        "scriptPubKey": _SCRIPT.hex(),
        "confirmations": 3,
        "spendable": True,
    }
    watched = {**entry, "vout": 2, "spendable": False}
    wallet, node = _wallet(listunspent=[entry, watched])
    utxos = wallet.utxos(0, include_immature_coinbase=True)
    assert utxos == (
        Utxo(
            OutPoint(bytes.fromhex(_TXID), 1),
            TxOut(50_000_000, ScriptPubKey(_SCRIPT, "regtest")),
            3,
        ),
    )
    assert utxos[0].tx_out.script_pub_key.address == address(_SCRIPT, "regtest")
    assert node.calls[-1] == (
        _NAME,
        "listunspent",
        {"minconf": 0, "query_options": {"include_immature_coinbase": True}},
    )


@pytest.mark.parametrize("field", ["vout", "confirmations"])
def test_a_listunspent_count_that_is_no_integer_is_refused(field: str) -> None:
    """A `vout` or a depth that is no integer is named by its shape alone."""
    entry = {
        "txid": _TXID,
        "vout": 1,
        "amount": Decimal("0.5"),
        "scriptPubKey": _SCRIPT.hex(),
        "confirmations": 3,
        "spendable": True,
        field: _WIF,
    }
    wallet, _ = _wallet(listunspent=[entry])
    with pytest.raises(TypeError, match=f"^{field} is a str of length 52") as excinfo:
        wallet.utxos()
    _assert_no_key(excinfo.value)


def test_a_boolean_is_no_integer() -> None:
    """JSON `true` is not read as the integer 1."""
    block = {"hash": "ab" * 32, "height": True}
    wallet, _ = _wallet(getwalletinfo={"lastprocessedblock": block})
    with pytest.raises(TypeError, match="^height is a bool, not an integer$"):
        wallet.tip()


def test_a_listunspent_that_is_no_list_is_refused() -> None:
    """Anything but a list is not read as entries."""
    wallet, _ = _wallet(listunspent={})
    with pytest.raises(TypeError, match="listunspent answered"):
        wallet.utxos()


def test_a_transaction_in_a_block_is_trusted() -> None:
    """Core answers no `trusted` for a confirmed transaction."""
    tx = _tx()
    wallet, node = _wallet(gettransaction={"hex": _hex(tx), "confirmations": 1})
    assert wallet.transaction(tx.id) == TxInfo(tx, trusted=True)
    assert node.calls[-1] == (_NAME, "gettransaction", [tx.id.hex()])


def test_a_transaction_in_no_block_is_as_trusted_as_core_says() -> None:
    """An unconfirmed transaction carries Core's own `trusted`."""
    tx = _tx()
    wallet, _ = _wallet(gettransaction={"hex": _hex(tx), "trusted": False})
    assert wallet.transaction(tx.id) == TxInfo(tx, trusted=False)


def test_tip_is_getwalletinfo_s_last_processed_block() -> None:
    """The height, and the hash in display order."""
    block = {"hash": "ab" * 32, "height": 7}
    wallet, _ = _wallet(getwalletinfo={"lastprocessedblock": block})
    assert wallet.tip() == BlockRef(7, bytes.fromhex("ab" * 32))


def test_sign_returns_the_signed_transaction() -> None:
    """The transaction goes out as hex, and comes back parsed."""
    unsigned, signed = _tx(), _tx(2_000)
    answer = {"hex": _hex(signed), "complete": True}
    wallet, node = _wallet(signrawtransactionwithwallet=answer)
    assert wallet.sign(unsigned) == signed
    assert node.calls[-1] == (_NAME, "signrawtransactionwithwallet", [_hex(unsigned)])


def test_sign_refuses_an_incomplete_signature() -> None:
    """`complete` false names how many inputs Core lists, not its text."""
    errors = [{"txid": _TXID, "vout": 0, "error": _WIF}]
    answer = {"hex": _hex(_tx()), "complete": False, "errors": errors}
    wallet, _ = _wallet(signrawtransactionwithwallet=answer)
    with pytest.raises(ValueError, match="a list of length 1") as excinfo:
        wallet.sign(_tx())
    _assert_no_key(excinfo.value)


def test_a_tip_height_that_is_no_integer_is_refused() -> None:
    """A height that is no integer is named by its shape alone."""
    block = {"hash": "ab" * 32, "height": _WIF}
    wallet, _ = _wallet(getwalletinfo={"lastprocessedblock": block})
    with pytest.raises(TypeError, match="^height is a str of length 52") as excinfo:
        wallet.tip()
    _assert_no_key(excinfo.value)


@pytest.mark.parametrize(
    "chain,network",
    [
        ("main", "mainnet"),
        ("test", "testnet"),
        ("testnet4", "testnet4"),
        ("signet", "signet"),
        ("regtest", "regtest"),
    ],
)
def test_the_network_is_the_node_s_chain_by_btclib_s_name(
    chain: str, network: str
) -> None:
    """Each chain `NodeAdapter.chain` can name is one of btclib's networks."""
    wallet, _ = _wallet(chain)
    assert wallet.network == network


@pytest.mark.parametrize("chain", ["regtest", "main"])
def test_spend_pays_a_script_s_address_on_the_node_s_chain(chain: str) -> None:
    """A script given as bytes, which btclib reads as mainnet's, is not."""
    tx = _tx()
    wallet, node = _wallet(
        chain,
        send={"txid": tx.id.hex(), "complete": True},
        gettransaction={"hex": _hex(tx), "trusted": True},
    )
    wallet.spend([TxOut(1, _SCRIPT)])
    payee = address(_SCRIPT, wallet.network)
    assert payee.startswith("bcrt1" if chain == "regtest" else "bc1")
    assert (_NAME, "send", {"outputs": [{payee: Decimal("1E-8")}]}) in node.calls


@pytest.mark.parametrize("chain,network", [("regtest", "regtest"), ("main", "mainnet")])
def test_each_returned_output_carries_the_node_s_chain(
    chain: str, network: str
) -> None:
    """`Tx.parse` reads mainnet; `transaction`, `sign` and `spend` do not."""
    tx = _tx()
    wallet, _ = _wallet(
        chain,
        gettransaction={"hex": _hex(tx), "trusted": True},
        signrawtransactionwithwallet={"hex": _hex(tx), "complete": True},
        send={"txid": tx.id.hex(), "complete": True},
    )
    assert Tx.parse(bytes.fromhex(_hex(tx))).vout[0].script_pub_key.network == "mainnet"
    returned = (
        wallet.transaction(tx.id).tx,
        wallet.sign(tx),
        wallet.spend([TxOut(1, _SCRIPT)]),
    )
    for each in returned:
        assert each == _tx(network=network)
        assert each.vout[0].script_pub_key.address == address(_SCRIPT, network)


def test_spend_is_send_and_returns_the_transaction() -> None:
    """Each output is an address and an amount in bitcoin, in order."""
    tx = _tx()
    wallet, node = _wallet(
        send={"txid": tx.id.hex(), "complete": True},
        gettransaction={"hex": _hex(tx), "trusted": True},
    )
    script = ScriptPubKey.from_address(address(_SCRIPT, "regtest"))
    assert wallet.spend([TxOut(150_000_000, script), TxOut(1, script)]) == tx
    payees = [{script.address: Decimal("1.5")}, {script.address: Decimal("1E-8")}]
    assert (_NAME, "send", {"outputs": payees}) in node.calls


def test_import_descriptor_is_importdescriptors_now() -> None:
    """One request, `timestamp` `"now"`, answered `success`."""
    wallet, node = _wallet(importdescriptors=[{"success": True}])
    wallet.import_descriptor("wpkh(x)#y")
    assert node.calls[-1] == (
        _NAME,
        "importdescriptors",
        [[{"desc": "wpkh(x)#y", "timestamp": "now"}]],
    )


def test_import_descriptor_refuses_a_failed_import_with_its_code_alone() -> None:
    """`success` false names the request and Core's code, never its text.

    Core's descriptor error quotes the key it refused, as v32.1 answers
    `wpkh( <wif>)`; the refusal carries none of it.
    """
    error = {"code": -5, "message": f"wpkh(): Key ' {_WIF}' is invalid"}
    wallet, _ = _wallet(importdescriptors=[{"success": False, "error": error}])
    with pytest.raises(ValueError, match="request 0, code -5") as excinfo:
        wallet.import_descriptor(f"wpkh( {_WIF})")
    _assert_no_key(excinfo.value)


def test_a_failed_import_without_an_error_object_says_so() -> None:
    """An `error` that is no object leaves the code unknown."""
    wallet, _ = _wallet(importdescriptors=[{"success": False, "error": _WIF}])
    with pytest.raises(ValueError, match="code None") as excinfo:
        wallet.import_descriptor(f"wpkh({_WIF})")
    _assert_no_key(excinfo.value)


def test_a_code_that_is_no_integer_is_not_relayed() -> None:
    """A `code` Core never sends, text, is named `None`."""
    error = {"code": _WIF, "message": _WIF}
    wallet, _ = _wallet(importdescriptors=[{"success": False, "error": error}])
    with pytest.raises(ValueError, match="code None$") as excinfo:
        wallet.import_descriptor(f"wpkh({_WIF})")
    _assert_no_key(excinfo.value)


@pytest.mark.parametrize("answer", [[], [_WIF, _WIF], _WIF])
def test_import_descriptor_refuses_an_answer_of_another_shape(answer: object) -> None:
    """Anything but one result is named by its shape, never its text."""
    wallet, _ = _wallet(importdescriptors=answer)
    with pytest.raises(TypeError, match="not one result") as excinfo:
        wallet.import_descriptor(f"wpkh({_WIF})")
    _assert_no_key(excinfo.value)


def test_a_result_of_another_shape_is_named_by_its_shape() -> None:
    """A result that is no object is named by its type and length."""
    wallet, _ = _wallet(importdescriptors=[_WIF])
    with pytest.raises(TypeError, match="a str of length 52") as excinfo:
        wallet.import_descriptor(f"wpkh({_WIF})")
    _assert_no_key(excinfo.value)


def test_unload_and_load_name_the_wallet_at_the_node() -> None:
    """`unloadwallet` and `loadwallet`, at the node's own endpoint."""
    wallet, node = _wallet()
    wallet.unload()
    wallet.load()
    assert node.calls == [("", "unloadwallet", [_NAME]), ("", "loadwallet", [_NAME])]


class _WalletlessNode:
    """A bitcoind built without its wallet, as `_has_wallet` reads one.

    It has its capabilities and nothing else, so a body that restarts it
    or calls it fails rather than skips.
    """

    capabilities = frozenset(Capability) - {Capability.NODE_WALLET, Capability.MINE}


def test_the_spend_body_skips_on_a_node_without_a_wallet() -> None:
    """It asks for `NODE_WALLET` before it restarts or calls the node."""
    node = _WalletlessNode()
    counts = SkipCounts()
    with pytest.raises(MissingCapabilityError, match="node does not declare node_"):
        spend_pays_a_script_given_as_bytes(
            lambda _: [cast("NodeAdapter", node)], BitcoindWalletFactory(), counts
        )
    assert list(counts) == [(Capability.NODE_WALLET.value, 1)]


# what `_client`'s client authenticates with, which its transport never reads
_CREDENTIAL = ("user", "pw")


def _client(
    error: object,
    failing: str = "importdescriptors",
    methods: list[str] | None = None,
) -> BitcoinCoreRpcClient:
    """Return a client whose node answers `failing` with `error`.

    The transport is in memory, so the reply goes through the client's
    own decoder, and Core's error reaches the wallet as the client raises it.
    Every other call is answered null. Each method called is appended to
    `methods`, where given.
    """

    def transport(request: Request, timeout: float) -> tuple[int, bytes]:
        del timeout
        assert isinstance(request.data, bytes)
        call = json.loads(request.data)
        if methods is not None:
            methods.append(call["method"])
        if call["method"] == failing:
            reply = {"jsonrpc": "2.0", "id": call["id"], "error": error}
        else:
            reply = {"jsonrpc": "2.0", "id": call["id"], "result": None}
        return 200, json.dumps(reply).encode()

    user, password = _CREDENTIAL
    return BitcoinCoreRpcClient(
        "http://127.0.0.1:1", user=user, password=password, transport=transport
    )


class _ClientNode:
    """A node reached through a real client over an in-memory transport."""

    chain = "regtest"

    def __init__(self, rpc: BitcoinCoreRpcClient) -> None:
        self.rpc = rpc


def test_a_thrown_import_error_is_refused_with_its_code_alone() -> None:
    """Core's JSON-RPC error quoting the key is not relayed, nor chained."""
    error = {"code": -5, "message": f"Key ' {_WIF}' is invalid", "data": _WIF}
    node = _ClientNode(_client(error))
    wallet = BitcoindWallet(cast("NodeAdapter", node), _NAME)
    with pytest.raises(
        ValueError, match="^descriptor not imported: request 0, code -5$"
    ) as excinfo:
        wallet.import_descriptor(f"wpkh( {_WIF})")
    _assert_no_key(excinfo.value)


def test_any_other_failure_of_the_import_names_its_type_alone() -> None:
    """An error object the client cannot read, and quotes, is not relayed."""
    node = _ClientNode(_client(_WIF))
    wallet = BitcoindWallet(cast("NodeAdapter", node), _NAME)
    with pytest.raises(
        RuntimeError, match="^importdescriptors raised FetchError$"
    ) as excinfo:
        wallet.import_descriptor(f"wpkh({_WIF})")
    _assert_no_key(excinfo.value)


def test_a_failed_sync_before_an_import_is_the_sync_s_own_error() -> None:
    """The sync's error names the sync, and the import is never called."""
    methods: list[str] = []
    error = {"code": -1, "message": "queue not delivered"}
    node = _ClientNode(_client(error, "syncwithvalidationinterfacequeue", methods))
    wallet = BitcoindWallet(cast("NodeAdapter", node), _NAME)
    with pytest.raises(
        RpcError, match="^syncwithvalidationinterfacequeue at "
    ) as excinfo:
        wallet.import_descriptor(f"wpkh({_WIF})")
    assert excinfo.value.code == -1
    _assert_no_key(excinfo.value)
    assert methods == ["syncwithvalidationinterfacequeue"]
