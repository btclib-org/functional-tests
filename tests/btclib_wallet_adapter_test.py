# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BtclibWallet`, driven over a chain in memory rather than a node.

Each method's translation of what its `Ledger` answers: what a run of
`tests/integration/*_btclib_wallet_test.py` does over a real bitcoind,
without starting one. The ledger itself is btclib-wallet's, tested
there.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, cast, override

import pytest
from bitcoin_core_rpc import BitcoinCoreRpcClient
from btclib.b58 import wif_from_prv_key
from btclib.fee import FeeRate
from btclib.key import PrvKeyData
from btclib.script import ScriptPubKey
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib_wallet.descriptors.descriptors import add_checksum
from btclib_wallet.exceptions import FetchError
from btclib_wallet.fetch.bitcoin_core import BitcoinCoreFetcher
from btclib_wallet.fetch.history import (
    BlockRef,
    HistoryTx,
    ScriptHistory,
    history_scripts,
)
from btclib_wallet.fetch.relay_fees import RelayFees
from btclib_wallet.ledger import Ledger

from btclib_functional_tests import btclib_wallet_adapter
from btclib_functional_tests.btclib_wallet_adapter import (
    BtclibWallet,
    BtclibWalletFactory,
    NodeBackend,
)
from btclib_functional_tests.wallet import (
    Balances,
    TxInfo,
    Utxo,
    WalletCapability,
)
from btclib_functional_tests.wallet import (
    BlockRef as WalletBlockRef,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator
    from pathlib import Path

    from btclib.alias import Octets

    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import WalletAdapter, WalletFactory

_VALUE = 100_000_000

# a regtest WIF, standing for any key a refusal could quote
_WIF = wif_from_prv_key(7, "regtest")

_FLOOR = FeeRate(sats_per_kvbyte=1_000)

# an address of nobody's, on regtest
_ADDRESS = ScriptPubKey.p2wpkh(PrvKeyData(11, network="regtest").pub).address


def _block_id(height: int) -> bytes:
    """Return the id of the block at `height` of the chain in memory."""
    return hashlib.sha256(height.to_bytes(4, "big")).digest()


class _Chain:
    """A regtest chain in memory: a `ChainBackend`, and blocks a test mines.

    `refuse` makes `broadcast` refuse, and `fail_fees` makes
    `get_relay_fees` fail. `starts` records the height each history read
    starts from.
    """

    def __init__(self) -> None:
        self.blocks: list[list[Tx]] = [[]]
        self.mempool: list[Tx] = []
        self.refuse = False
        self.fail_fees = False
        self.starts: list[int] = []

    def get_block_count(self) -> int:
        return len(self.blocks) - 1

    def get_relay_fees(self) -> RelayFees:
        if self.fail_fees:
            err_msg = "no relay fees"
            raise FetchError(err_msg)
        return RelayFees(_FLOOR, _FLOOR, _FLOOR)

    def broadcast(self, tx: Tx) -> bytes:
        if self.refuse:
            err_msg = "refused"
            raise FetchError(err_msg)
        self.mempool.append(tx)
        return tx.id

    def mine(self, *txs: Tx) -> None:
        """Mine a block of `txs` and of the mempool."""
        self.blocks.append([*txs, *self.mempool])
        self.mempool = []

    def get_history(
        self, scripts: Iterable[Octets | ScriptPubKey], start_height: int = 0
    ) -> ScriptHistory:
        wanted = history_scripts(scripts)
        self.starts.append(start_height)
        every = [tx for block in self.blocks for tx in block] + self.mempool
        paid = {
            OutPoint(tx.id, vout)
            for tx in every
            for vout, tx_out in enumerate(tx.vout)
            if tx_out.script_pub_key.script in wanted
        }

        def touches(tx: Tx) -> bool:
            return any(o.script_pub_key.script in wanted for o in tx.vout) or any(
                tx_in.prev_out in paid for tx_in in tx.vin
            )

        tip = self.get_block_count()
        start = min(start_height, tip)
        txs = [
            HistoryTx(tx, BlockRef(height, _block_id(height)))
            for height in range(start, tip + 1)
            for tx in self.blocks[height]
            if touches(tx)
        ]
        txs += [HistoryTx(tx, None) for tx in self.mempool if touches(tx)]
        return ScriptHistory(
            BlockRef(start, _block_id(start)),
            BlockRef(tip, _block_id(tip)),
            tuple(txs),
        )


class _Node:
    """What `BtclibWallet` reads of a node: its chain."""

    chain = "regtest"


def _payment(addr: str, value: int = _VALUE) -> Tx:
    """Return a payment of `value` to `addr`, from an output of nobody's."""
    vin = [TxIn(OutPoint(b"\x01" * 32, 0))]
    return Tx(2, 0, vin, [TxOut(value, ScriptPubKey.from_address(addr))])


def _made(tmp_path: Path) -> tuple[BtclibWallet, _Chain, BtclibWalletFactory]:
    """Return a wallet over a chain of one block, its chain, and its factory."""
    chain = _Chain()
    chain.mine()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    return factory.create(cast("NodeAdapter", _Node()), "w"), chain, factory


@pytest.fixture
def made(tmp_path: Path) -> Iterator[tuple[BtclibWallet, _Chain]]:
    """Yield `_made`'s wallet and chain, every wallet closed after."""
    wallet, chain, factory = _made(tmp_path)
    yield wallet, chain
    factory.close()


@pytest.fixture
def funded(made: tuple[BtclibWallet, _Chain]) -> tuple[BtclibWallet, _Chain, Tx]:
    """Return `made`'s wallet, paid in a block, its chain, the payment."""
    wallet, chain = made
    payment = _payment(wallet.new_address())
    chain.mine(payment)
    return wallet, chain, payment


def test_the_wallet_and_its_factory_are_the_protocol_s(tmp_path: Path) -> None:
    """Both satisfy the protocols a body is written against."""
    chain = _Chain()
    factory: WalletFactory = BtclibWalletFactory(tmp_path, lambda _: chain)
    node = cast("NodeAdapter", _Node())
    wallet: WalletAdapter = factory.create(node, "w")
    assert wallet.name == "w"
    assert wallet.node is node
    assert isinstance(factory, BtclibWalletFactory)
    factory.close()


def test_the_factory_declares_no_node_capability_and_every_wallet_one(
    tmp_path: Path,
) -> None:
    """The node needs nothing named; the wallet has every capability."""
    factory = BtclibWalletFactory(tmp_path)
    assert factory.node_capabilities == frozenset()
    assert factory.capabilities == set(WalletCapability)
    assert factory.node_args == ()


def test_two_wallets_of_one_name_have_a_file_each(tmp_path: Path) -> None:
    """The file is named after the wallet and the order it was made in."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    node = cast("NodeAdapter", _Node())
    factory.create(node, "w")
    factory.create(node, "w")
    assert sorted(p.name for p in tmp_path.glob("*.sqlite")) == [
        "0-w.sqlite",
        "1-w.sqlite",
    ]
    factory.close()


class _Unreachable(_Chain):
    """A chain whose node does not answer for its height."""

    @override
    def get_block_count(self) -> int:
        err_msg = "no height"
        raise FetchError(err_msg)


def test_a_wallet_not_made_leaves_no_file(tmp_path: Path) -> None:
    """The node's height is read before the file is made."""
    chains = iter([_Unreachable(), _Chain()])
    factory = BtclibWalletFactory(tmp_path, lambda _: next(chains))
    node = cast("NodeAdapter", _Node())
    with pytest.raises(FetchError, match="no height"):
        factory.create(node, "w")
    assert not list(tmp_path.iterdir())
    assert factory.create(node, "w").name == "w"
    factory.close()


def test_a_source_not_added_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger is closed and its file removed, so the path is free."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    node = cast("NodeAdapter", _Node())

    def refuse(*_: object) -> int:
        err_msg = "no source"
        raise RuntimeError(err_msg)

    with monkeypatch.context() as patched:
        patched.setattr(Ledger, "add_source", refuse)
        with pytest.raises(RuntimeError, match="no source"):
            factory.create(node, "w")
    assert not list(tmp_path.iterdir())
    assert factory.create(node, "w").name == "w"
    factory.close()


def test_a_new_wallet_reads_no_block_before_its_node_s_height(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """Its first sync reads from the height the node had when it was made."""
    wallet, chain = made
    chain.mine()
    assert wallet.tip() == WalletBlockRef(2, _block_id(2))
    assert chain.starts[0] == 1


def test_a_new_source_is_a_bip84_account_of_its_network() -> None:
    """Coin type 1 on a test network, 0 on mainnet."""
    regtest = btclib_wallet_adapter._new_source("regtest")
    mainnet = btclib_wallet_adapter._new_source("mainnet")
    assert "/84h/1h/0h/" in str(regtest.descriptor(0))
    assert "/84h/0h/0h/" in str(mainnet.descriptor(0))
    assert regtest.branches == (0, 1)
    assert not regtest.is_watch_only


def test_a_payment_counts_in_every_read(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """The balance, the buckets, the outputs and the transaction."""
    wallet, _, payment = funded
    assert wallet.balance() == _VALUE
    assert wallet.balance(2) == 0
    assert wallet.balances() == Balances(_VALUE, 0, 0, 0)
    (utxo,) = wallet.utxos()
    assert utxo == Utxo(OutPoint(payment.id, 0), utxo.tx_out, 1)
    assert utxo.tx_out.value == _VALUE
    assert utxo.tx_out.script_pub_key.network == "regtest"
    info = wallet.transaction(payment.id)
    assert info == TxInfo(info.tx, True, 1, _VALUE, None, False, frozenset())
    assert info.tx.id == payment.id


def test_a_transaction_not_the_wallet_s_is_refused(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """The ledger's refusal is a `ValueError`."""
    wallet, _ = made
    with pytest.raises(ValueError, match="not a ledger transaction"):
        wallet.transaction(b"\x02" * 32)


def test_a_spend_pays_and_is_read_back(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """The payment is broadcast, and the wallet holds its change."""
    wallet, chain, _ = funded
    payee = ScriptPubKey.from_address(wallet.new_change_address())
    tx = wallet.spend([TxOut(_VALUE // 2, payee)])
    assert chain.mempool == [tx]
    info = wallet.transaction(tx.id)
    assert info.fee is not None
    assert info.fee > 0
    assert info.confirmations == 0
    assert wallet.balance() == _VALUE - info.fee


def test_a_refused_spend_is_the_transaction_the_wallet_kept(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """The ledger records it before the refusal; it is not trusted."""
    wallet, chain, _ = funded
    chain.refuse = True
    tx = wallet.spend([TxOut(_VALUE // 2, ScriptPubKey.from_address(_ADDRESS))])
    assert chain.mempool == []
    info = wallet.transaction(tx.id)
    assert info.trusted is False
    assert info.confirmations == 0


def test_a_failure_before_a_spend_is_recorded_is_raised(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """A `FetchError` that leaves nothing recorded is the caller's."""
    wallet, chain, _ = funded
    chain.fail_fees = True
    with pytest.raises(FetchError, match="no relay fees"):
        wallet.spend([TxOut(_VALUE // 2, ScriptPubKey.from_address(_ADDRESS))])


def test_sign_signs_the_wallet_s_inputs(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """Each input spending the wallet's output gets its witness."""
    wallet, _, payment = funded
    out = TxOut(_VALUE // 2, ScriptPubKey.from_address(_ADDRESS))
    unsigned = Tx(2, 0, [TxIn(OutPoint(payment.id, 0))], [out])
    signed = wallet.sign(unsigned)
    assert signed.id == unsigned.id
    assert signed.vin[0].script_witness.stack
    assert signed.vout[0].script_pub_key.network == "regtest"
    # the transaction it was given is left unsigned
    assert not unsigned.vin[0].script_witness.stack


def test_sign_refuses_an_input_it_cannot_sign(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """An input spending nothing the wallet holds is left unsigned."""
    wallet, _ = made
    out = TxOut(1_000, ScriptPubKey.from_address(_ADDRESS))
    tx = Tx(2, 0, [TxIn(OutPoint(b"\x03" * 32, 0))], [out])
    with pytest.raises(ValueError, match="^inputs left unsigned$") as excinfo:
        wallet.sign(tx)
    assert excinfo.value.__cause__ is None


def test_an_imported_descriptor_makes_its_payments_the_wallet_s(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """A payment to its script, after the import, counts."""
    wallet, chain = made
    wallet.import_descriptor(add_checksum(f"wpkh({_WIF})"))
    script = ScriptPubKey.p2wpkh(PrvKeyData(7, network="regtest").pub)
    chain.mine(_payment(script.address))
    assert wallet.balance() == _VALUE


def test_a_refused_import_names_the_exception_s_type_alone(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """No text of the refusal, which can quote the key, and nothing chained."""
    wallet, _ = made
    with pytest.raises(ValueError, match="^descriptor not imported: ") as excinfo:
        wallet.import_descriptor(f"wpkh({_WIF})#notachecksum")
    assert _WIF not in str(excinfo.value)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_a_locked_output_is_not_listed(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """`Ledger.lock` keeps it out of `utxos`."""
    wallet, _, payment = funded
    wallet.lock(OutPoint(payment.id, 0))
    assert wallet.utxos() == ()


def test_abandon_marks_a_payment_out_of_the_mempool(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """A payment the mempool dropped is abandoned, and its coin counts."""
    wallet, chain, _ = funded
    payee = ScriptPubKey.from_address(_ADDRESS)
    tx = wallet.spend([TxOut(_VALUE // 2, payee)])
    chain.mempool = []
    wallet.abandon(tx.id)
    assert wallet.transaction(tx.id).abandoned is True
    assert wallet.balance() == _VALUE


def test_abandon_refuses_a_confirmed_transaction(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """The ledger's refusal is a `ValueError`."""
    wallet, _, payment = funded
    with pytest.raises(ValueError, match="cannot be abandoned"):
        wallet.abandon(payment.id)


def test_unload_reads_the_node_then_load_answers_from_the_file(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """A payment in the mempool at the unload is still the wallet's after it."""
    wallet, chain = made
    payment = _payment(wallet.new_address())
    chain.mempool.append(payment)
    wallet.unload()
    with pytest.raises(ValueError, match="^wallet w is unloaded$"):
        wallet.balance()
    chain.mempool = []
    wallet.load()
    assert wallet.transaction(payment.id).confirmations == 0


def test_close_reads_no_node_and_closes_once(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """A fixture's close reads nothing, and a second one does nothing."""
    wallet, chain = made
    reads = len(chain.starts)
    wallet.close()
    wallet.close()
    assert len(chain.starts) == reads
    with pytest.raises(ValueError, match="unloaded"):
        _ = wallet.ledger


# what `_ClientNode`'s client authenticates with, which nothing reads
_CREDENTIAL = ("user", "pw")


class _ClientNode:
    """A node whose client goes nowhere: `NodeBackend` builds one a call."""

    chain = "regtest"

    def __init__(self) -> None:
        self.clients = 0

    @property
    def rpc(self) -> BitcoinCoreRpcClient:
        self.clients += 1
        user, password = _CREDENTIAL
        return BitcoinCoreRpcClient("http://127.0.0.1:1", user=user, password=password)


def test_node_backend_asks_a_new_fetcher_each_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Each call builds a fetcher over a new client, on the node's network."""
    seen: list[tuple[str, object]] = []

    def record(name: str, answer: object) -> object:
        def method(self: BitcoinCoreFetcher, *args: object) -> object:
            seen.append((name, (self.network, *args)))
            return answer

        return method

    history = ScriptHistory(BlockRef(0, b"\x00" * 32), BlockRef(0, b"\x00" * 32), ())
    fees = RelayFees(_FLOOR, _FLOOR, _FLOOR)
    monkeypatch.setattr(BitcoinCoreFetcher, "get_history", record("history", history))
    monkeypatch.setattr(BitcoinCoreFetcher, "broadcast", record("broadcast", b"id"))
    monkeypatch.setattr(BitcoinCoreFetcher, "get_relay_fees", record("fees", fees))
    monkeypatch.setattr(BitcoinCoreFetcher, "get_block_count", record("count", 5))
    node = _ClientNode()
    backend = NodeBackend(cast("NodeAdapter", node))
    tx = _payment(_ADDRESS)
    assert backend.get_history([b"\x51"], 3) is history
    assert backend.broadcast(tx) == b"id"
    assert backend.get_relay_fees() is fees
    assert backend.get_block_count() == 5
    assert node.clients == 4
    assert seen == [
        ("history", ("regtest", [b"\x51"], 3)),
        ("broadcast", ("regtest", tx)),
        ("fees", ("regtest",)),
        ("count", ("regtest",)),
    ]
