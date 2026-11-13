# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BtclibWallet`: `wallet.WalletAdapter` over btclib-wallet's `Ledger`.

The target's side of a wallet test
([ISS 619](https://github.com/btclib-org/functional-tests/issues/619)):
each wallet is a `btclib_wallet.ledger.Ledger` in this process, in a file
of its own, reading the chain from its node.
`CONTRIBUTING.md`'s *The one constraint* says why a wallet may run in
process where a node may not.

A new wallet holds one source: a BIP84 account of a random seed, its
receiving and change branches, read from the node's height when the
wallet is made, as Core's `createwallet` reads no block before it.

Every method that reads the wallet or spends from it first calls
`Ledger.sync`, as `wallet.py`'s module docstring asks. `new_address`,
`new_change_address` and `lock` read no chain, and sync nothing.

The ledger reads the node through `NodeBackend`, which builds a
`BitcoinCoreFetcher` for each call. A client built once would keep the
cookie of the node's first start: a restart writes a new cookie, and
the old one no longer authenticates.

The ledger is given no fee estimator, so it pays `FALLBACK_FEE_RATE`,
raised to the node's mempool minimum: the rate `BitcoindAdapter` starts
bitcoind's own wallet with (`-fallbackfee`).

What differs from bitcoind's wallet here is the ledger's:

- the ledger broadcasts nothing it was not asked to, so a node's
  restart resubmits no transaction of the wallet, where Core's wallet
  resubmits its own on load (`Ledger.resubmit`'s docstring);
- a descriptor imported with a WIF is watch-only: btclib-wallet's
  descriptor parser keeps the private key of an extended key, and of no
  other (`descriptors.parse`).
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterable, Sequence
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Protocol

from btclib.alias import Octets
from btclib.exceptions import BTClibValueError
from btclib.fee import FeeRate
from btclib.network import NETWORKS
from btclib.script.script_pub_key import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxOut
from btclib_wallet.bip32.bip32 import rootxprv_from_seed
from btclib_wallet.descriptors.descriptors import add_checksum
from btclib_wallet.exceptions import FetchError
from btclib_wallet.fetch.bitcoin_core import BitcoinCoreFetcher
from btclib_wallet.fetch.fetcher import tx_for_network
from btclib_wallet.fetch.history import ScriptHistory
from btclib_wallet.fetch.relay_fees import RelayFees
from btclib_wallet.ledger import Ledger, TxState
from btclib_wallet.psbt.psbt import Psbt, extract_tx, finalize
from btclib_wallet.wallet.descriptor_wallet import DescriptorWallet

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
    "FALLBACK_FEE_RATE",
    "BtclibWallet",
    "BtclibWalletFactory",
    "ChainBackend",
    "NodeBackend",
]

# `BitcoindAdapter`'s `-fallbackfee=0.0002`, in satoshi per 1000 vbytes
FALLBACK_FEE_RATE = FeeRate(sats_per_kvbyte=20_000)


class ChainBackend(Protocol):
    """What a ledger reads its node through, and what a wallet asks of it.

    `btclib_wallet.fetch`'s `HistoryReader`, `Broadcaster` and
    `RelayFeeReader`, and the height of the node's tip.
    """

    def get_history(
        self, scripts: Iterable[Octets | ScriptPubKey], start_height: int = 0
    ) -> ScriptHistory:
        """Return the history of `scripts`, from `start_height` to the tip."""
        ...

    def broadcast(self, tx: Tx) -> bytes:
        """Send `tx` to the node, and return its id."""
        ...

    def get_relay_fees(self) -> RelayFees:
        """Return the relay fee floors of the node."""
        ...

    def get_block_count(self) -> int:
        """Return the height of the node's tip."""
        ...


class NodeBackend:
    """`ChainBackend` over a node's RPC: a new `BitcoinCoreFetcher` a call."""

    def __init__(self, node: NodeAdapter) -> None:
        self._node = node

    def _fetcher(self) -> BitcoinCoreFetcher:
        """Return a fetcher over a new client of the node."""
        return BitcoinCoreFetcher(self._node.rpc, network_of(self._node))

    def get_history(
        self, scripts: Iterable[Octets | ScriptPubKey], start_height: int = 0
    ) -> ScriptHistory:
        """Return `BitcoinCoreFetcher.get_history`'s answer."""
        return self._fetcher().get_history(scripts, start_height)

    def broadcast(self, tx: Tx) -> bytes:
        """Send `tx` with `BitcoinCoreFetcher.broadcast`."""
        return self._fetcher().broadcast(tx)

    def get_relay_fees(self) -> RelayFees:
        """Return `BitcoinCoreFetcher.get_relay_fees`'s answer."""
        return self._fetcher().get_relay_fees()

    def get_block_count(self) -> int:
        """Return `BitcoinCoreFetcher.get_block_count`'s answer."""
        return self._fetcher().get_block_count()


def _new_source(network: str) -> DescriptorWallet:
    """Return a BIP84 account of a random seed, receiving and change."""
    xprv = rootxprv_from_seed(secrets.token_bytes(32), NETWORKS[network].bip32_prv)
    coin_type = 0 if network == "mainnet" else 1
    descriptor = add_checksum(f"wpkh({xprv}/84h/{coin_type}h/0h/<0;1>/*)")
    return DescriptorWallet.from_descriptor(descriptor, network, {})


class BtclibWallet:
    """One ledger of btclib-wallet, as `wallet.WalletAdapter` drives it.

    Made in a new file at `path`, holding the module docstring's one
    source, born at the height `backend` reads.
    """

    def __init__(
        self, node: NodeAdapter, name: str, path: Path, backend: ChainBackend
    ) -> None:
        self._node = node
        self._name = name
        self._path = path
        self._backend = backend
        self._ledger: Ledger | None = None
        self._open(create=True)
        self.ledger.add_source(_new_source(self.network), backend.get_block_count())

    @property
    def node(self) -> NodeAdapter:
        """The node this wallet reads the chain from and broadcasts to."""
        return self._node

    @property
    def name(self) -> str:
        """The name the wallet was created with."""
        return self._name

    @property
    def ledger(self) -> Ledger:
        """The ledger, for what only btclib-wallet answers.

        It is no part of `wallet.WalletAdapter`, as `BitcoindWallet.rpc`
        is not. A call through it gets no sync.

        :raises ValueError: the wallet is unloaded.
        """
        if self._ledger is None:
            err_msg = f"wallet {self._name} is unloaded"
            raise ValueError(err_msg)
        return self._ledger

    def _open(self, *, create: bool) -> None:
        """Make the ledger's file, or open it again."""
        opener = Ledger.create if create else Ledger.load
        self._ledger = opener(
            self._path,
            network_of(self._node),
            self._backend,
            broadcaster=self._backend,
            relay_fee_reader=self._backend,
            fallback_fee_rate=FALLBACK_FEE_RATE,
        )

    def _synced(self) -> Ledger:
        """Return the ledger, once it has read what its node holds."""
        ledger = self.ledger
        ledger.sync()
        return ledger

    def new_address(self) -> str:
        """Return `Ledger.new_address`'s answer."""
        return self.ledger.new_address()

    def new_change_address(self) -> str:
        """Return `Ledger.new_change_address`'s answer."""
        return self.ledger.new_change_address()

    def balance(self, min_depth: int = 0) -> int:
        """Return the trusted bucket at `min_depth`, as `getbalance` counts it.

        `Ledger.balances` with `include_nonmempool` off: an output a
        transaction outside the mempool spends is left out.
        """
        return self._synced().balances(min_depth, include_nonmempool=False).trusted

    def balances(self) -> Balances:
        """Return `Ledger.balances`'s buckets."""
        found = self._synced().balances()
        return Balances(
            found.trusted, found.untrusted_pending, found.immature, found.nonmempool
        )

    def utxos(
        self, min_depth: int = 1, *, include_immature_coinbase: bool = False
    ) -> tuple[Utxo, ...]:
        """Return `Ledger.utxos`'s outputs."""
        found = self._synced().utxos(
            min_depth, include_immature_coinbase=include_immature_coinbase
        )
        return tuple(Utxo(u.outpoint, u.tx_out, u.confirmations) for u in found)

    def transaction(self, txid: bytes) -> TxInfo:
        """Return what `Ledger.transaction` holds of the transaction.

        :raises ValueError: the transaction is not the wallet's.
        """
        info = self._synced().transaction(txid)
        return TxInfo(
            info.wallet_tx.tx,
            info.trusted,
            info.confirmations,
            info.amount,
            info.fee,
            info.wallet_tx.state is TxState.ABANDONED,
            info.wallet_conflicts,
        )

    def tip(self) -> BlockRef:
        """Return the block the ledger is synced to."""
        tip = self.ledger.sync().tip
        return BlockRef(tip.height, tip.block_id)

    def sign(self, tx: Tx) -> Tx:
        """Return `tx` with every input `Ledger.sign` signs finalized.

        Each output carries the node's chain.

        :raises ValueError: an input is left unsigned.
        """
        # `Psbt.from_tx` empties the inputs of the transaction it is given
        unsigned = Tx.parse(tx.serialize(include_witness=False, check_validity=False))
        ledger = self._synced()
        try:
            final = finalize(ledger.sign(Psbt.from_tx(unsigned)))
        except BTClibValueError:
            err_msg = "inputs left unsigned"
            raise ValueError(err_msg) from None
        # no fee ceiling: `signrawtransactionwithwallet` checks no fee
        return tx_for_network(extract_tx(final, max_fee_rate=None), self.network)

    @property
    def network(self) -> str:
        """The node's chain by btclib's name, the one each address is for."""
        return network_of(self._node)

    def spend(self, outputs: Sequence[TxOut]) -> Tx:
        """Pay `outputs` with `Ledger.spend`, and return the transaction.

        A refused broadcast leaves the transaction in the ledger, which
        records it before it broadcasts: the transaction returned is
        then the one it recorded last.
        """
        ledger = self._synced()
        held = len(ledger.transactions)
        try:
            return ledger.spend(outputs)
        except FetchError:
            if len(ledger.transactions) == held:
                raise
        return ledger.transactions[-1].wallet_tx.tx

    def import_descriptor(self, descriptor: str) -> None:
        """Add `descriptor` with `Ledger.add_source`, reading no earlier block.

        :raises ValueError: btclib-wallet refused the descriptor; the
            message names the exception's type alone, since its text can
            quote a key.
        """
        ledger = self._synced()
        refusal: Exception
        try:
            source = DescriptorWallet.from_descriptor(descriptor, self.network, {})
        # any failure to read a descriptor carrying a key may quote it
        except Exception as exc:  # noqa: BLE001
            refusal = ValueError(f"descriptor not imported: {type(exc).__name__}")
        else:
            ledger.add_source(source, None)
            return
        # raised here, outside the `except` block, so it has no context
        raise refusal

    def lock(self, outpoint: OutPoint) -> None:
        """Lock the output with `Ledger.lock`."""
        self.ledger.lock(outpoint)

    def abandon(self, txid: bytes) -> None:
        """Abandon the transaction with `Ledger.abandon`.

        :raises ValueError: the ledger refused it.
        """
        self._synced().abandon(txid)

    def unload(self) -> None:
        """Close the ledger and its file, once it has read its node.

        Core's wallet reads each transaction its node accepts as it is
        accepted, so an unloaded one holds what its node had: a payment
        that leaves the mempool while it is unloaded is still its own.
        """
        self._synced().close()
        self._ledger = None

    def load(self) -> None:
        """Open the ledger's file again."""
        self._open(create=False)

    def close(self) -> None:
        """Close the ledger where it is open, reading no node.

        What a fixture calls once the test ends, its nodes maybe stopped.
        """
        if self._ledger is not None:
            self._ledger.close()
            self._ledger = None


class BtclibWalletFactory:
    """`wallet.WalletFactory` for `BtclibWallet`: a file each, in `directory`.

    `backend` builds what each wallet reads its node through,
    `NodeBackend` unless a caller names another. `close` closes every
    wallet this factory made, and a fixture calls it once the test ends.
    """

    def __init__(
        self,
        directory: Path,
        backend: Callable[[NodeAdapter], ChainBackend] = NodeBackend,
    ) -> None:
        self._directory = directory
        self._backend = backend
        self._made: list[BtclibWallet] = []

    @property
    def node_capabilities(self) -> AbstractSet[Capability]:
        """None: the wallet reads the chain over RPC calls no capability names.

        The calls `BitcoinCoreFetcher` makes.
        """
        return frozenset()

    @property
    def capabilities(self) -> AbstractSet[WalletCapability]:
        """Every `wallet.WalletCapability`: the ledger has each."""
        return frozenset(WalletCapability)

    @property
    def node_args(self) -> tuple[str, ...]:
        """None: the node runs as it would with no wallet."""
        return ()

    def create(self, node: NodeAdapter, name: str) -> BtclibWallet:
        """Make a ledger of one new source in a new file, and return it.

        The file is named after the wallet and the order it was made in,
        so two nodes' wallets of one name have a file each.
        """
        path = self._directory / f"{len(self._made)}-{name}.sqlite"
        wallet = BtclibWallet(node, name, path, self._backend(node))
        self._made.append(wallet)
        return wallet

    def close(self) -> None:
        """Close every wallet this factory made."""
        for wallet in self._made:
            wallet.close()
