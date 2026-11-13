# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BitcoindWallet`: `wallet.WalletAdapter` over bitcoind's own wallet.

The oracle's side of a wallet test: each method is one call of Core's
wallet RPC, at the wallet's own endpoint (`/wallet/<name>`), its answer
read into the protocol's types. Amounts cross in satoshi on this side
and in bitcoin on the RPC's.

Before each call, `syncwithvalidationinterfacequeue` lets the node
deliver every block and mempool notification it has queued to its
wallets, as Core's own `sync_mempools` (`test_framework.py`) does. A
wallet RPC already waits for the blocks (`BlockUntilSyncedToCurrentChain`,
`src/wallet/wallet.h`), and not for a transaction relayed into the
mempool.

`BitcoindWalletFactory` is the `wallet.WalletFactory` a test module
hands its body, over a node that declares `Capability.NODE_WALLET`.

A refusal here carries no text of Core's answer: a wallet's answer, an
import's error among them, can quote a private key. It names the shape
of an answer, or Core's error code, instead (section 7 of the
organization's standard, *A refusal carries no secret*). A call
carrying a descriptor is refused the same way whether Core answers its
error in the result or as the call's own JSON-RPC error, and the
refusal is raised after the `except` block, so that it chains nothing
that holds Core's text. A number is read as Core sends it, never
converted from text, so that no conversion's own error quotes what it
refused.

Each address crossing to the RPC is spelled for the node's own chain,
`NodeAdapter.chain`: a script's bytes carry no chain, and btclib
spells one it reads from bytes, as `Tx.parse` reads every output, for
mainnet. Each output returned, of a `Utxo` or of a transaction, carries
the node's chain for the same reason.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Sized
from collections.abc import Set as AbstractSet
from decimal import Decimal

from bitcoin_core_rpc import BitcoinCoreRpcClient, RpcError
from btclib.amount import btc_from_sats, sats_from_btc
from btclib.script.script_pub_key import ScriptPubKey, address
from btclib.tx import OutPoint, Tx, TxOut

from btclib_functional_tests.capability import Capability
from btclib_functional_tests.node import NodeAdapter
from btclib_functional_tests.wallet import (
    Balances,
    BlockRef,
    TxInfo,
    Utxo,
    WalletCapability,
)

__all__ = [
    "BitcoindWallet",
    "BitcoindWalletFactory",
]

# btclib's network for each chain `NodeAdapter.chain` names, in Core's
# own `-chain=` vocabulary
_NETWORKS = {
    "main": "mainnet",
    "test": "testnet",
    "testnet4": "testnet4",
    "signet": "signet",
    "regtest": "regtest",
}


def _shape(answer: object) -> str:
    """Return what a refusal says of `answer`: its type, and its length."""
    if isinstance(answer, Sized):
        return f"a {type(answer).__name__} of length {len(answer)}"
    return f"a {type(answer).__name__}"


def _sats(amount: object) -> int:
    """Return an RPC amount, a `Decimal` of bitcoin, in satoshi."""
    if not isinstance(amount, Decimal):
        err_msg = f"not an amount: {_shape(amount)}"
        raise TypeError(err_msg)
    return sats_from_btc(amount)


def _on_chain(tx: Tx, network: str) -> Tx:
    """Return `tx` with each output's script carrying `network`."""
    vout = [
        TxOut(tx_out.value, ScriptPubKey(tx_out.script_pub_key.script, network))
        for tx_out in tx.vout
    ]
    return Tx(tx.version, tx.lock_time, tx.vin, vout)


def _code(value: object) -> int | None:
    """Return `value` where it is an error code Core could send, else None."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _int(value: object, field: str) -> int:
    """Return `value` where it is a JSON integer, or raise naming `field`."""
    if isinstance(value, bool) or not isinstance(value, int):
        err_msg = f"{field} is {_shape(value)}, not an integer"
        raise TypeError(err_msg)
    return value


def _dict(answer: object, method: str) -> dict[str, object]:
    """Return `answer` where it is a JSON object, or raise naming `method`."""
    if not isinstance(answer, dict):
        err_msg = f"{method} answered {_shape(answer)}, not an object"
        raise TypeError(err_msg)
    return answer


def _check_import(answer: object) -> None:
    """Refuse an `importdescriptors` answer that is not one success."""
    if not isinstance(answer, list) or len(answer) != 1:
        err_msg = f"importdescriptors answered {_shape(answer)}, not one result"
        raise TypeError(err_msg)
    result = _dict(answer[0], "importdescriptors")
    if result["success"] is not True:
        error = result.get("error")
        code = _code(error.get("code")) if isinstance(error, dict) else None
        err_msg = f"descriptor not imported: request 0, code {code}"
        raise ValueError(err_msg)


class BitcoindWallet:
    """One of bitcoind's wallets, as `wallet.WalletAdapter` drives it."""

    def __init__(self, node: NodeAdapter, name: str) -> None:
        self._node = node
        self._name = name

    @property
    def node(self) -> NodeAdapter:
        """The bitcoind holding this wallet."""
        return self._node

    @property
    def name(self) -> str:
        """The name the wallet was created with."""
        return self._name

    @property
    def rpc(self) -> BitcoinCoreRpcClient:
        """A client of this wallet's own endpoint, for Core's own answers.

        A body reads through it what only Core answers: an error's text,
        or a result's whole shape. No method of the protocol calls it, and
        no call through it waits for the node's queue.
        """
        return self._node.rpc.for_wallet(self._name)

    @property
    def network(self) -> str:
        """The node's chain by btclib's name, the one each address is for."""
        return _NETWORKS[self._node.chain]

    def _call(
        self,
        method: str,
        params: Sequence[object] | Mapping[str, object] | None = None,
    ) -> object:
        """Call `method` at this wallet, once the node's queue is delivered."""
        self._sync()
        return self.rpc.call(method, params)

    def _sync(self) -> None:
        """Let the node deliver its queued notifications to its wallets."""
        self._node.rpc.call("syncwithvalidationinterfacequeue")

    def new_address(self) -> str:
        """Return `getnewaddress`'s answer."""
        return str(self._call("getnewaddress"))

    def new_change_address(self) -> str:
        """Return `getrawchangeaddress`'s answer."""
        return str(self._call("getrawchangeaddress"))

    def balance(self, min_depth: int = 0) -> int:
        """Return `getbalance`'s answer at `minconf` `min_depth`, in satoshi."""
        return _sats(self._call("getbalance", {"minconf": min_depth}))

    def balances(self) -> Balances:
        """Return `getbalances`'s `mine`, in satoshi.

        `nonmempool` is `None` where Core answers none.
        """
        mine = _dict(_dict(self._call("getbalances"), "getbalances")["mine"], "mine")
        return Balances(
            _sats(mine["trusted"]),
            _sats(mine["untrusted_pending"]),
            _sats(mine["immature"]),
            _sats(mine["nonmempool"]) if "nonmempool" in mine else None,
        )

    def utxos(
        self, min_depth: int = 1, *, include_immature_coinbase: bool = False
    ) -> tuple[Utxo, ...]:
        """Return the entries of `listunspent` the wallet can spend.

        Each output's script carries the node's chain, `network`.
        """
        answer = self._call(
            "listunspent",
            {
                "minconf": min_depth,
                "query_options": {
                    "include_immature_coinbase": include_immature_coinbase
                },
            },
        )
        if not isinstance(answer, list):
            err_msg = f"listunspent answered {_shape(answer)}, not a list"
            raise TypeError(err_msg)
        entries = [_dict(entry, "listunspent") for entry in answer]
        network = self.network
        return tuple(
            Utxo(
                OutPoint(
                    bytes.fromhex(str(entry["txid"])), _int(entry["vout"], "vout")
                ),
                TxOut(
                    _sats(entry["amount"]),
                    ScriptPubKey(bytes.fromhex(str(entry["scriptPubKey"])), network),
                ),
                _int(entry["confirmations"], "confirmations"),
            )
            for entry in entries
            if entry["spendable"] is True
        )

    def transaction(self, txid: bytes) -> TxInfo:
        """Return the transaction and its trust from `gettransaction`.

        Core answers `trusted` only for a transaction in no block
        (`WalletTxToJSON`, `src/wallet/rpc/transactions.cpp`): one in a
        block is trusted. Each output carries the node's chain, `network`.
        """
        answer = _dict(self._call("gettransaction", [txid.hex()]), "gettransaction")
        tx = _on_chain(Tx.parse(bytes.fromhex(str(answer["hex"]))), self.network)
        return TxInfo(tx, answer.get("trusted", True) is True)

    def tip(self) -> BlockRef:
        """Return `getwalletinfo`'s `lastprocessedblock`."""
        info = _dict(self._call("getwalletinfo"), "getwalletinfo")
        block = _dict(info["lastprocessedblock"], "lastprocessedblock")
        return BlockRef(
            _int(block["height"], "height"), bytes.fromhex(str(block["hash"]))
        )

    def sign(self, tx: Tx) -> Tx:
        """Return `signrawtransactionwithwallet`'s transaction.

        Each output carries the node's chain, `network`.

        :raises ValueError: Core answered `complete` false; the message
            names how many inputs its `errors` lists.
        """
        hex_tx = tx.serialize(include_witness=True, check_validity=False).hex()
        answer = _dict(
            self._call("signrawtransactionwithwallet", [hex_tx]),
            "signrawtransactionwithwallet",
        )
        if answer["complete"] is not True:
            err_msg = f"inputs left unsigned: {_shape(answer.get('errors'))}"
            raise ValueError(err_msg)
        return _on_chain(Tx.parse(bytes.fromhex(str(answer["hex"]))), self.network)

    def spend(self, outputs: Sequence[TxOut]) -> Tx:
        """Pay `outputs` with `send`, and return the transaction it made.

        Each payee is the address of its output's script on the node's
        chain, whatever chain the `TxOut` itself was made for, and the
        transaction returned is `transaction`'s. A refused broadcast
        leaves the transaction in the wallet where the node runs with
        `BitcoindWalletFactory.node_args`.
        """
        network = self.network
        payees = [
            {
                address(tx_out.script_pub_key.script, network): btc_from_sats(
                    tx_out.value
                )
            }
            for tx_out in outputs
        ]
        answer = _dict(self._call("send", {"outputs": payees}), "send")
        txid = bytes.fromhex(str(answer["txid"]))
        return self.transaction(txid).tx

    def import_descriptor(self, descriptor: str) -> None:
        """Add `descriptor` with `importdescriptors`, `timestamp` `"now"`.

        :raises ValueError: Core refused the import, in its result or as
            the call's own error; the message carries the error's code
            where that is an integer, `None` otherwise, and none of its
            text.
        :raises RuntimeError: the call failed otherwise; the message names
            the exception's type alone.
        """
        request = [[{"desc": descriptor, "timestamp": "now"}]]
        # outside the `try`: the sync carries no descriptor, and its own
        # failure is not the import's
        self._sync()
        refusal: Exception
        try:
            answer = self.rpc.call("importdescriptors", request)
        except RpcError as exc:
            code = _code(exc.code)
            refusal = ValueError(f"descriptor not imported: request 0, code {code}")
        # any other failure of a call carrying a key may quote it too
        except Exception as exc:  # noqa: BLE001
            refusal = RuntimeError(f"importdescriptors raised {type(exc).__name__}")
        else:
            _check_import(answer)
            return
        # raised here, outside the `except` blocks, so it has no context
        raise refusal

    def unload(self) -> None:
        """Unload the wallet with `unloadwallet`."""
        self._node.rpc.call("unloadwallet", [self._name])

    def load(self) -> None:
        """Load the wallet again with `loadwallet`."""
        self._node.rpc.call("loadwallet", [self._name])


class BitcoindWalletFactory:
    """`wallet.WalletFactory` for `BitcoindWallet`."""

    @property
    def node_capabilities(self) -> AbstractSet[Capability]:
        """`Capability.NODE_WALLET`: the wallet is the node's own."""
        return frozenset({Capability.NODE_WALLET})

    @property
    def capabilities(self) -> AbstractSet[WalletCapability]:
        """Every `wallet.WalletCapability`: Core's wallet RPCs have each."""
        return frozenset(WalletCapability)

    @property
    def node_args(self) -> tuple[str, ...]:
        """`-walletrejectlongchains=0`, so that `spend` keeps what is refused.

        bitcoind's wallet otherwise refuses to make a transaction its
        node's mempool would refuse for its unconfirmed ancestors
        (`DEFAULT_WALLET_REJECT_LONG_CHAINS`, `src/wallet/wallet.h`). With
        it off, the wallet makes and keeps the transaction, and the node
        refuses it, as `wallet.WalletAdapter.spend` says.
        """
        return ("-walletrejectlongchains=0",)

    def create(self, node: NodeAdapter, name: str) -> BitcoindWallet:
        """Create the wallet with `createwallet`, loaded on every start.

        `load_on_startup` is what Core's own `init_wallet`
        (`test_framework.py`) passes, so a restarted node loads it again.
        """
        node.rpc.call("createwallet", {"wallet_name": name, "load_on_startup": True})
        return BitcoindWallet(node, name)
