# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BitcoindWallet`: `wallet.WalletAdapter` over bitcoind's own wallet.

The oracle's side of a wallet test: each method calls Core's wallet
RPC at the wallet's own endpoint (`/wallet/<name>`) and reads the
answer into the protocol's types. Each makes one call there, except
`spend`, which makes two, `send` and then `gettransaction` through
`transaction`, and each call there follows a sync at the node's own
endpoint, below. `unload` and `load` are the exception to the
endpoint: `unloadwallet` and `loadwallet` are the node's, called at its
own endpoint with no sync, naming the wallet in their parameters.
Amounts cross in satoshi on this side and in bitcoin on the RPC's.

Before each call it makes at the wallet's endpoint, the adapter calls
`syncwithvalidationinterfacequeue` at the node's, so that what the node
has accepted, a transaction relayed into its mempool included, has
reached the wallet before the call.

`BitcoindWalletFactory` is the `wallet.WalletFactory` a test module
hands its body, over a node that declares `Capability.NODE_WALLET`.

A refusal here, but for the sync's, carries no text of Core's answer: a
wallet's answer, an import's error among them, can quote a private key.
It names the shape of an answer, or Core's error code, instead (section
7 of the organization's standard, *A refusal carries no secret*). A call
carrying a descriptor is refused the same way whether Core answers its
error in the result or as the call's own JSON-RPC error, and the refusal
is raised after the `except` block, so that it chains nothing that holds
Core's text. A number is read as Core sends it, never converted from
text, so that no conversion's own error quotes what it refused.

A failed call at the wallet's endpoint is refused by Core's code, or by
the type of the client's exception, alone, since a wallet's name can be
a secret too: `_by_code` says where the client's text holds it. The sync
before such a call, at the node's endpoint, whose path holds no name,
raises the client's own error, Core's text included.

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
    network_of,
)

__all__ = [
    "BitcoindWallet",
    "BitcoindWalletFactory",
]

# `src/rpc/protocol.h`: `RPC_INVALID_ADDRESS_OR_KEY`, what
# `abandontransaction` answers a transaction it does not abandon
_RPC_INVALID_ADDRESS_OR_KEY = -5


def _shape(answer: object) -> str:
    """Return what a refusal says of `answer`: its type, and its length."""
    if isinstance(answer, Sized):
        return f"a {type(answer).__name__} of length {len(answer)}"
    return f"a {type(answer).__name__}"


def _txid_hex(txid: object) -> str:
    """Return `txid` in hex, refusing all but 32 bytes before Core sees it.

    Core's `ParseHashV` refuses any other string with a message that quotes
    it (`src/rpc/util.cpp`, bitcoin/bitcoin@f0de87f040, the v32.1 tag), and
    a key given by mistake as an id would be printed. Of a 32-byte id,
    `gettransaction`, `lockunspent` and `abandontransaction` quote nothing.
    The refusal names the type and the length alone, as `_shape` does.

    :raises TypeError: `txid` is not bytes.
    :raises ValueError: `txid` is not 32 bytes.
    """
    if not isinstance(txid, bytes):
        err_msg = f"not a transaction id: {_shape(txid)}"
        raise TypeError(err_msg)
    if len(txid) != 32:
        err_msg = f"not a transaction id: {_shape(txid)}"
        raise ValueError(err_msg)
    return txid.hex()


def _sats(amount: object) -> int:
    """Return an RPC amount, a `Decimal` of bitcoin, in satoshi."""
    if not isinstance(amount, Decimal):
        err_msg = f"not an amount: {_shape(amount)}"
        raise TypeError(err_msg)
    return sats_from_btc(amount)


def _signed_sats(amount: object) -> int:
    """Return an RPC amount that may be negative, in satoshi."""
    if isinstance(amount, Decimal) and amount < 0:
        return -_sats(-amount)
    return _sats(amount)


def _on_chain(tx: Tx, network: str) -> Tx:
    """Return `tx` with each output's script carrying `network`."""
    vout = [
        TxOut(tx_out.value, ScriptPubKey(tx_out.script_pub_key.script, network))
        for tx_out in tx.vout
    ]
    return Tx(tx.version, tx.lock_time, tx.vin, vout)


def _code(value: object) -> int | None:
    """Return `value` where it is an error code Core could send, else None."""
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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


def _by_code(
    rpc: BitcoinCoreRpcClient,
    method: str,
    params: Sequence[object] | Mapping[str, object] | None,
) -> object:
    """Return `rpc.call(method, params)`, refusing it by Core's code alone.

    For a call whose refusal can quote what it carries, or name the wallet
    it is made at. Core's `createwallet` and `loadwallet` quote the wallet's
    name, or a path ending in it (`src/wallet/rpc/wallet.cpp`,
    `src/wallet/walletdb.cpp`), and `send` quotes an address it refuses
    (`src/rpc/rawtransaction_util.cpp`), at bitcoin/bitcoin@f0de87f040, the
    v32.1 tag. `unloadwallet` quotes no name there, though it carries one.
    The client's own text of a failure can quote it too: it names a wallet's
    endpoint, whose path holds the wallet's name, and it quotes whole an
    error object it cannot read.

    :raises ValueError: Core refused the call; the message names the
        method and Core's code where that is an integer, `None` otherwise.
    :raises RuntimeError: the call failed otherwise; the message names the
        method and the exception's type alone.
    """
    refusal: Exception
    try:
        return rpc.call(method, params)
    except RpcError as exc:
        refusal = ValueError(f"{method} refused: code {_code(exc.code)}")
    # any other failure of the call may quote what it carries too
    except Exception as exc:  # noqa: BLE001
        refusal = RuntimeError(f"{method} raised {type(exc).__name__}")
    # raised here, outside the `except` blocks, so it has no context
    raise refusal


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

        It is no part of `wallet.WalletAdapter`. A body reads through it,
        where its wallet is a `BitcoindWallet`, what only Core answers: an
        error's text, or a result's whole shape. The protocol's methods
        make their wallet calls through it too, each after the adapter's
        sync of the node's validation queue. A call a body makes through
        it gets no such sync.
        """
        return self._node.rpc.for_wallet(self._name)

    @property
    def network(self) -> str:
        """The node's chain by btclib's name, the one each address is for."""
        return network_of(self._node)

    def _call(
        self,
        method: str,
        params: Sequence[object] | Mapping[str, object] | None = None,
    ) -> object:
        """Call `method` at this wallet, once the node's queue is delivered.

        :raises ValueError: Core refused the call (`_by_code`).
        :raises RuntimeError: the call failed otherwise (`_by_code`).
        """
        # outside `_by_code`: the sync is at the node's endpoint, which
        # holds no name, and its own failure is not the call's
        self._sync()
        return _by_code(self.rpc, method, params)

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
            _signed_sats(mine["nonmempool"]) if "nonmempool" in mine else None,
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
        """Return what `gettransaction` answers of the transaction.

        Core answers `trusted` only for a transaction in no block
        (`WalletTxToJSON`, `src/wallet/rpc/transactions.cpp`): one in a
        block is trusted. Its `fee` is negative, and absent where the
        transaction spends none of the wallet's coins. It reports
        `abandoned` on each entry of `details`, so a transaction with no
        entry reads as not abandoned. Each output carries the node's
        chain, `network`.

        :raises TypeError: `txid` is not bytes (`_txid_hex`).
        :raises ValueError: `txid` is not 32 bytes (`_txid_hex`).
        """
        answer = _dict(
            self._call("gettransaction", [_txid_hex(txid)]), "gettransaction"
        )
        tx = _on_chain(Tx.parse(bytes.fromhex(str(answer["hex"]))), self.network)
        details = answer["details"]
        conflicts = answer["walletconflicts"]
        if not isinstance(details, list) or not isinstance(conflicts, list):
            err_msg = "gettransaction answered no list of details or conflicts"
            raise TypeError(err_msg)
        return TxInfo(
            tx,
            answer.get("trusted", True) is True,
            _int(answer["confirmations"], "confirmations"),
            _signed_sats(answer["amount"]),
            -_signed_sats(answer["fee"]) if "fee" in answer else None,
            any(_dict(entry, "details")["abandoned"] is True for entry in details),
            frozenset(bytes.fromhex(str(txid)) for txid in conflicts),
        )

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

        :raises ValueError: Core refused `send`, as it refuses two outputs
            paying one script; the message names its code and no address
            (`_by_code`).
        :raises RuntimeError: `send` failed otherwise (`_by_code`).
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

    def lock(self, outpoint: OutPoint) -> None:
        """Lock the output with `lockunspent`.

        :raises TypeError: its `tx_id` is not bytes (`_txid_hex`).
        :raises ValueError: its `tx_id` is not 32 bytes (`_txid_hex`).
        """
        txid = _txid_hex(outpoint.tx_id)
        self._call("lockunspent", [False, [{"txid": txid, "vout": outpoint.vout}]])

    def abandon(self, txid: bytes) -> None:
        """Abandon the transaction with `abandontransaction`.

        :raises ValueError: Core refused it with
            `RPC_INVALID_ADDRESS_OR_KEY`, as it refuses a transaction in a
            block or the mempool and one not the wallet's. The message
            names the code and not the transaction, by section 7's *A
            refusal carries no secret* of the organization's standard.
            Before Core sees it, an id that is not 32 bytes is refused
            too, and one that is not bytes raises `TypeError` (`_txid_hex`).
        :raises RuntimeError: Core answered another code, which refuses no
            transaction, or the call failed otherwise. The message names
            the code, or the exception's type, alone, as `_by_code` does.
        """
        hex_txid = _txid_hex(txid)
        # outside the `try`: the sync is at the node's endpoint, as in `_call`
        self._sync()
        refusal: Exception
        try:
            self.rpc.call("abandontransaction", [hex_txid])
        except RpcError as exc:
            code = _code(exc.code)
            if code == _RPC_INVALID_ADDRESS_OR_KEY:
                err_msg = "not abandoned: Core answered RPC_INVALID_ADDRESS_OR_KEY"
                refusal = ValueError(err_msg)
            else:
                refusal = RuntimeError(f"abandontransaction failed: code {code}")
        # any other failure of a call at the wallet's endpoint may name it
        except Exception as exc:  # noqa: BLE001
            refusal = RuntimeError(f"abandontransaction raised {type(exc).__name__}")
        else:
            return
        # raised here, outside the `except` blocks, so it has no context
        raise refusal

    def unload(self) -> None:
        """Unload the wallet with `unloadwallet`, and off the startup list.

        So a restart of the node leaves it unloaded until `load`.

        :raises ValueError: Core refused the call; the message names its
            code and not the wallet (`_by_code`).
        :raises RuntimeError: the call failed otherwise (`_by_code`).
        """
        _by_code(
            self._node.rpc,
            "unloadwallet",
            {"wallet_name": self._name, "load_on_startup": False},
        )

    def load(self) -> None:
        """Load the wallet again with `loadwallet`, and on the startup list.

        :raises ValueError: Core refused the call, as it refuses a wallet
            already loaded; the message names its code and not the wallet
            (`_by_code`).
        :raises RuntimeError: the call failed otherwise (`_by_code`).
        """
        _by_code(
            self._node.rpc,
            "loadwallet",
            {"filename": self._name, "load_on_startup": True},
        )


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

        :raises ValueError: Core refused the call, as it refuses a name
            whose wallet exists; the message names its code and not the
            name (`_by_code`).
        :raises RuntimeError: the call failed otherwise (`_by_code`).
        """
        _by_code(
            node.rpc, "createwallet", {"wallet_name": name, "load_on_startup": True}
        )
        return BitcoindWallet(node, name)
