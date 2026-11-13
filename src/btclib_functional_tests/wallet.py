# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`WalletAdapter`: a wallet under test, beside the node it runs over.

[ISS 544](https://github.com/btclib-org/functional-tests/issues/544):
Core's `wallet_*.py` drive bitcoind's built-in wallet, and this protocol
lets one test body drive any wallet instead. A body is written once, in
btclib's names: amounts in satoshi, transactions as `btclib.tx.Tx`, and
each method named after the method of btclib-wallet's `Ledger` that
answers it, where there is one. Each implementation translates, and
`bitcoind_wallet.py` is the one over bitcoind's RPC wallet.
`CONTRIBUTING.md`'s *Wallets* says how a test reaches its wallets.

Every method that reads the wallet or spends from it first lets the
wallet catch up with what its node has accepted, blocks and relayed
transactions alike, so that the call sees them, and no test can leave
out a sync that only another implementation needs.
"""

from __future__ import annotations

# every name an annotation below uses is imported for real, so that the
# documentation build resolves each signature's annotations together
# (`docs/source/conf.py`'s comment on `nitpick_ignore` has the cascade)
from collections.abc import Sequence
from collections.abc import Set as AbstractSet
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from btclib.tx import OutPoint, Tx, TxOut

from btclib_functional_tests.capability import Capability
from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "Balances",
    "BlockRef",
    "TxInfo",
    "Utxo",
    "WalletAdapter",
    "WalletCapability",
    "WalletFactory",
]


class WalletCapability(Enum):
    """What a test may ask of a wallet beyond the protocol's core.

    Counted by `capability.SkipCounts` beside the node's capabilities,
    under the same rule 4 of ISS btclib-org/btclib#2220. A value differs
    from every `capability.Capability` value, since the count is keyed
    on it.

    `RELOAD` -- `WalletAdapter.unload` and `WalletAdapter.load`: the
    wallet is closed and opened again, and keeps every transaction it
    held, as Core's `unloadwallet` and `loadwallet` do.
    `IMPORT_DESCRIPTOR` -- `WalletAdapter.import_descriptor`: the wallet
    takes one more descriptor while in use, and an output of a
    transaction it already holds becomes its own without a rescan, as
    Core's `importdescriptors` with `timestamp: "now"` does.
    """

    RELOAD = "wallet_reload"
    IMPORT_DESCRIPTOR = "wallet_import_descriptor"


@dataclass(frozen=True)
class Balances:
    """A wallet's balance buckets, in satoshi: Core's `getbalances` `mine`.

    `nonmempool` is `None` where the wallet keeps no such bucket, as
    bitcoind before `v32.0` (bitcoin/bitcoin#33671).
    """

    trusted: int
    untrusted_pending: int
    immature: int
    nonmempool: int | None


@dataclass(frozen=True)
class BlockRef:
    """The block a wallet is synced to: Core's `lastprocessedblock`.

    `block_id` is in display order, as `getbestblockhash` answers it.
    """

    height: int
    block_id: bytes


@dataclass(frozen=True)
class Utxo:
    """An output a wallet can spend: an entry of Core's `listunspent`."""

    outpoint: OutPoint
    tx_out: TxOut
    confirmations: int


@dataclass(frozen=True)
class TxInfo:
    """What a wallet holds of a transaction: Core's `gettransaction`.

    `trusted` is Core's `CachedTxIsTrusted` (`src/wallet/receive.cpp`):
    a confirmed transaction is trusted, and an unconfirmed one where the
    wallet sent it, it is in the mempool, and each input spends a trusted
    output of the wallet.
    """

    tx: Tx
    trusted: bool


class WalletAdapter(Protocol):
    """One wallet under test, and the node it reads the chain from.

    Every output it returns, of a `Utxo` or of a transaction, carries the
    node's chain, so that its script's address is that chain's. `spend`
    pays each output's script on the node's chain, whatever chain its
    `TxOut` was made for.
    """

    @property
    def node(self) -> NodeAdapter:
        """The node this wallet reads the chain from and broadcasts to."""

    @property
    def name(self) -> str:
        """The name the wallet was created with."""

    def new_address(self) -> str:
        """Hand out a new receiving address: Core's `getnewaddress`."""

    def new_change_address(self) -> str:
        """Hand out a new change address: Core's `getrawchangeaddress`."""

    def balance(self, min_depth: int = 0) -> int:
        """Return the trusted balance at `min_depth` confirmations or more.

        Core's `getbalance` with `minconf`, in satoshi.
        """

    def balances(self) -> Balances:
        """Return the balance buckets: Core's `getbalances` `mine`."""

    def utxos(
        self, min_depth: int = 1, *, include_immature_coinbase: bool = False
    ) -> tuple[Utxo, ...]:
        """Return the outputs the wallet can spend: Core's `listunspent`.

        :param min_depth: Core's `minconf`.
        :param include_immature_coinbase: Core's query option of that name.
        """

    def transaction(self, txid: bytes) -> TxInfo:
        """Return what the wallet holds of a transaction.

        :param txid: in display order, as `Tx.id` returns it.
        """

    def tip(self) -> BlockRef:
        """Return the block the wallet is synced to."""

    def sign(self, tx: Tx) -> Tx:
        """Return `tx` with every input the wallet's keys spend signed.

        Core's `signrawtransactionwithwallet`.

        :raises ValueError: an input is left unsigned.
        """

    def spend(self, outputs: Sequence[TxOut]) -> Tx:
        """Pay `outputs` from the wallet's coins, broadcast, and return it.

        Core's `send`. A transaction the node refuses stays in the
        wallet, outside the mempool, and is not trusted.
        """

    def import_descriptor(self, descriptor: str) -> None:
        """Add `descriptor`, reading no block before the wallet's tip.

        `WalletCapability.IMPORT_DESCRIPTOR`.

        :param descriptor: with its checksum.
        """

    def unload(self) -> None:
        """Close the wallet: `WalletCapability.RELOAD`."""

    def load(self) -> None:
        """Open the wallet again: `WalletCapability.RELOAD`."""


class WalletFactory(Protocol):
    """How a test makes wallets of one implementation."""

    @property
    def node_capabilities(self) -> AbstractSet[Capability]:
        """What a node needs to declare before a wallet is put on it."""

    @property
    def capabilities(self) -> AbstractSet[WalletCapability]:
        """What every wallet this factory makes can do."""

    @property
    def node_args(self) -> tuple[str, ...]:
        """The options a node under one of these wallets starts with.

        A body appends them to the `extra_args` of every start of such a
        node, so that the wallet behaves as `WalletAdapter` says.
        """

    def create(self, node: NodeAdapter, name: str) -> WalletAdapter:
        """Make a new wallet named `name` over `node`, and return it."""
