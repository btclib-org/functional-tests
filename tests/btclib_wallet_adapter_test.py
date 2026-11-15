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
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, override

import pytest
from bitcoin_core_rpc import BitcoinCoreRpcClient, RpcError
from btclib.b58 import wif_from_prv_key
from btclib.exceptions import BTClibValueError
from btclib.fee import FeeRate
from btclib.key import PrvKeyData
from btclib.network import NETWORKS
from btclib.script import ScriptPubKey, serialize
from btclib.script.engine import PAY_TO_ANCHOR, verify_input
from btclib.script.witness import Witness
from btclib.tx import OutPoint, Tx, TxIn, TxOut
from btclib_wallet.bip32.bip32 import rootxprv_from_seed
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
from btclib_wallet.psbt.psbt import Psbt, extract_tx, finalize
from btclib_wallet.psbt_signer import SoftwareSigner

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
    from collections.abc import Callable, Iterable, Iterator

    from btclib.alias import Octets

    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import WalletAdapter, WalletFactory

_VALUE = 100_000_000

# a regtest WIF, standing for any key a refusal could quote
_WIF = wif_from_prv_key(7, "regtest")

_FLOOR = FeeRate(sats_per_kvbyte=1_000)

# an address of nobody's, on regtest
_ADDRESS = ScriptPubKey.p2wpkh(PrvKeyData(11, network="regtest").pub).address


# a private key, passed by mistake where a transaction id belongs
_KEY = (7).to_bytes(32, "big")


def _assert_secret_free(exc: BaseException, secret: bytes | str) -> None:
    """Assert `secret` is in no text, argument or attribute of `exc`.

    Each spelling a refusal could print of a secret in bytes is asked, and
    `exc` chains nothing:
    `__context__` is asked as well as `__cause__`, since `raise ... from
    None` hides the first from a traceback and keeps it.
    """
    spellings = (
        (secret,)
        if isinstance(secret, str)
        else (secret.hex(), secret[::-1].hex(), repr(secret))
    )
    for spelling in spellings:
        assert spelling not in str(exc)
        assert all(spelling not in repr(arg) for arg in exc.args)
        assert all(spelling not in repr(value) for value in vars(exc).values())
    assert exc.__cause__ is None
    assert exc.__context__ is None


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

    def get_coin_parent(self, outpoint: OutPoint) -> Tx | None:
        """Core's `findCoins`: a spend in the mempool hides no coin."""
        mined = [tx for block in self.blocks for tx in block]
        if all(tx.id != outpoint.tx_id for tx in self.mempool) and any(
            tx_in.prev_out == outpoint for tx in mined for tx_in in tx.vin
        ):
            return None
        every = mined + self.mempool
        paying = (tx for tx in every if tx.id == outpoint.tx_id)
        parent = next(paying, None)
        lacking = parent is None or outpoint.vout >= len(parent.vout)
        return None if lacking else parent

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


def _refused_source(
    failure: BaseException,
) -> Callable[..., int]:
    """Return an `add_source` that raises `failure`."""

    def refuse(*_: object) -> int:
        raise failure

    return refuse


def _unremovable(self: Path, *_: object) -> None:
    """Stand in for `Path.unlink` where the file system refuses it."""
    raise PermissionError(13, "Permission denied", str(self))


def test_a_source_not_added_leaves_no_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ledger is closed and its file removed, so the path is free.

    The refusal names the failure's type, and neither the name, here a
    key given by mistake, nor the path that holds it.
    """
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    node = cast("NodeAdapter", _Node())
    with monkeypatch.context() as patched:
        patched.setattr(Ledger, "add_source", _refused_source(RuntimeError(_WIF)))
        with pytest.raises(
            ValueError, match="^wallet not created: RuntimeError$"
        ) as excinfo:
            factory.create(node, _WIF)
    _assert_secret_free(excinfo.value, _WIF)
    assert not list(tmp_path.iterdir())
    assert factory.create(node, _WIF).name == _WIF
    factory.close()


def test_a_file_not_removed_is_named_by_its_error_s_type(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `OSError` of the removal quotes the path: its type is named alone."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    with monkeypatch.context() as patched:
        patched.setattr(Ledger, "add_source", _refused_source(RuntimeError()))
        patched.setattr(Path, "unlink", _unremovable)
        with pytest.raises(
            ValueError,
            match="^wallet not created: RuntimeError, its file not removed: "
            "PermissionError$",
        ) as excinfo:
            factory.create(cast("NodeAdapter", _Node()), _WIF)
    _assert_secret_free(excinfo.value, _WIF)
    factory.close()


def test_an_interrupt_while_a_wallet_is_made_removes_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An exception that is no `Exception` is raised as it is."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    with monkeypatch.context() as patched:
        patched.setattr(Ledger, "add_source", _refused_source(KeyboardInterrupt()))
        with pytest.raises(KeyboardInterrupt) as excinfo:
            factory.create(cast("NodeAdapter", _Node()), "w")
    assert not hasattr(excinfo.value, "__notes__")
    assert not list(tmp_path.iterdir())


def test_an_interrupt_notes_a_file_not_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The interrupt is raised as it is, with a note naming the error's type."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    with monkeypatch.context() as patched:
        patched.setattr(Ledger, "add_source", _refused_source(KeyboardInterrupt()))
        patched.setattr(Path, "unlink", _unremovable)
        with pytest.raises(KeyboardInterrupt) as excinfo:
            factory.create(cast("NodeAdapter", _Node()), _WIF)
    assert excinfo.value.__notes__ == ["its file not removed: PermissionError"]
    _assert_secret_free(excinfo.value, _WIF)


def test_a_second_wallet_at_one_path_names_no_path(tmp_path: Path) -> None:
    """Another factory's wallet of the same name in the same directory."""
    chain = _Chain()
    first = BtclibWalletFactory(tmp_path, lambda _: chain)
    second = BtclibWalletFactory(tmp_path, lambda _: chain)
    node = cast("NodeAdapter", _Node())
    first.create(node, _WIF)
    with pytest.raises(
        ValueError, match="^wallet not created: BTClibValueError$"
    ) as excinfo:
        second.create(node, _WIF)
    _assert_secret_free(excinfo.value, _WIF)
    first.close()


def test_a_loaded_wallet_is_not_loaded_again(tmp_path: Path) -> None:
    """The refusal names neither the wallet nor its file."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    wallet = factory.create(cast("NodeAdapter", _Node()), _WIF)
    with pytest.raises(ValueError, match="^the wallet is already loaded$") as excinfo:
        wallet.load()
    _assert_secret_free(excinfo.value, _WIF)
    factory.close()


def test_a_file_the_ledger_refuses_is_named_by_its_error_s_type(
    tmp_path: Path,
) -> None:
    """A file gone while the wallet is unloaded: its path is not quoted."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    wallet = factory.create(cast("NodeAdapter", _Node()), _WIF)
    wallet.unload()
    for path in tmp_path.iterdir():
        path.unlink()
    with pytest.raises(ValueError, match="^wallet not loaded: ") as excinfo:
        wallet.load()
    _assert_secret_free(excinfo.value, _WIF)
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
    """A key given as the id is in no text of the refusal; nothing chained."""
    wallet, _ = made
    with pytest.raises(
        ValueError, match="^not a transaction of the wallet$"
    ) as excinfo:
        wallet.transaction(_KEY)
    _assert_secret_free(excinfo.value, _KEY)


def test_abandon_refuses_a_transaction_not_the_wallet_s(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """A key given as the id is in no text of the refusal; nothing chained."""
    wallet, _ = made
    with pytest.raises(
        ValueError, match="^not abandoned: not a transaction of the wallet$"
    ) as excinfo:
        wallet.abandon(_KEY)
    _assert_secret_free(excinfo.value, _KEY)


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
    assert excinfo.value.__context__ is None


class _Signed:
    """Alice's coin and bob's in one transaction, bob's input signed by bob.

    `where` bob's coin is: in a `block`, in the `mempool`, or in the
    transaction that pays `alice's`, which her ledger holds. `kind` is
    bob's script: P2WPKH, P2SH-P2WPKH, whose input carries a `script_sig`
    and a witness, or P2PKH, whose input carries a `script_sig` alone.
    """

    def __init__(
        self, tmp_path: Path, where: str = "block", *, kind: str = "wpkh"
    ) -> None:
        self.chain = _Chain()
        self.factory = BtclibWalletFactory(tmp_path, lambda _: self.chain)
        node = cast("NodeAdapter", _Node())
        self.alice = self.factory.create(node, "alice")
        bob = self.factory.create(node, "bob")
        bob_address = bob.new_address()
        if kind != "wpkh":
            xprv = rootxprv_from_seed(bytes(32), NETWORKS["regtest"].bip32_prv)
            descriptor = "sh(wpkh(K))" if kind == "sh-wpkh" else "pkh(K)"
            bob.import_descriptor(add_checksum(descriptor.replace("K", f"{xprv}/0/*")))
            bob_address = bob.ledger.new_address(1)
        alice_coin = _payment(self.alice.new_address())
        bob_coin = _payment(bob_address)
        if where == "alice's":
            alice_coin.vout.append(bob_coin.vout[0])
            bob_coin = alice_coin
            self.chain.mine(alice_coin)
        elif where == "mempool":
            self.chain.mine(alice_coin)
            self.chain.mempool.append(bob_coin)
        else:
            self.chain.mine(alice_coin, bob_coin)
        self.bob_outpoint = OutPoint(bob_coin.id, len(bob_coin.vout) - 1)
        self.prevouts = [alice_coin.vout[0], bob_coin.vout[-1]]
        vin = [TxIn(OutPoint(alice_coin.id, 0)), TxIn(self.bob_outpoint)]
        self.tx = Tx(2, 0, vin, [TxOut(_VALUE, ScriptPubKey.from_address(_ADDRESS))])
        # bob signs his input, told what alice's spends
        psbt = Psbt.from_tx(Tx.parse(self.tx.serialize(include_witness=False)))
        psbt.inputs[0].non_witness_utxo = alice_coin
        bob.balance()
        bob_in = bob.ledger.sign(psbt).inputs[1]
        ((pub_key, sig),) = bob_in.partial_sigs.items()
        self.sig, self.pub_key = sig, pub_key
        if kind == "pkh":
            self.tx.vin[1].script_sig = serialize([sig, pub_key])
        else:
            self.tx.vin[1].script_witness = Witness([sig, pub_key])
        if kind == "sh-wpkh":
            self.tx.vin[1].script_sig = serialize([bob_in.redeem_script])
        verify_input(self.prevouts, self.tx, 1)

    def spend_bob_s_coin(self) -> Tx:
        """Return a transaction spending bob's coin, which he does not sign."""
        out = TxOut(_VALUE // 2, self.prevouts[1].script_pub_key)
        return Tx(2, 0, [TxIn(self.bob_outpoint)], [out])


def _assert_kept(given: _Signed) -> None:
    """Assert alice signs her input and keeps bob's, byte for byte."""
    signed = given.alice.sign(given.tx)
    assert signed.id == given.tx.id
    assert signed.vin[1].script_sig == given.tx.vin[1].script_sig
    assert signed.vin[1].script_witness == given.tx.vin[1].script_witness
    verify_input(given.prevouts, signed, 0)
    verify_input(given.prevouts, signed, 1)
    given.factory.close()


@pytest.mark.parametrize("where", ["block", "mempool", "alice's"])
def test_sign_keeps_an_input_another_party_signed(tmp_path: Path, where: str) -> None:
    """Alice signs her input and keeps bob's valid signature on his coin.

    As Core's `signrawtransactionwithwallet` does, wherever the node
    holds bob's coin.
    """
    _assert_kept(_Signed(tmp_path, where))


@pytest.mark.parametrize("where", ["block", "mempool"])
def test_sign_keeps_a_signature_on_a_coin_spent_in_the_mempool(
    tmp_path: Path, where: str
) -> None:
    """Core's `findCoins` reads the coin through the mempool, spends unread."""
    given = _Signed(tmp_path, where)
    given.chain.mempool.append(given.spend_bob_s_coin())
    _assert_kept(given)


def test_sign_keeps_a_nested_segwit_input_s_script_sig(tmp_path: Path) -> None:
    """Bob's P2SH-P2WPKH input keeps its `script_sig` beside its witness."""
    given = _Signed(tmp_path, kind="sh-wpkh")
    assert given.tx.vin[1].script_sig
    _assert_kept(given)


def test_sign_keeps_a_legacy_input_s_script_sig(tmp_path: Path) -> None:
    """Bob's P2PKH input keeps its `script_sig`, and has no witness."""
    given = _Signed(tmp_path, kind="pkh")
    assert not given.tx.vin[1].script_witness.stack
    _assert_kept(given)


def test_sign_refuses_a_signature_that_does_not_verify(tmp_path: Path) -> None:
    """Bob's signature, altered, is refused; nothing chained."""
    given = _Signed(tmp_path)
    altered = bytearray(given.sig)
    altered[-2] ^= 1
    given.tx.vin[1].script_witness = Witness([bytes(altered), given.pub_key])
    with pytest.raises(ValueError, match="^inputs left unsigned$") as excinfo:
        given.alice.sign(given.tx)
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None
    given.factory.close()


# secp256k1's group order: a DER signature of S is valid with N - S
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


def _high_s(sig: bytes) -> bytes:
    """Return `sig`, DER and a hash type, with its S as N - S.

    Valid under consensus, and refused by Core's LOW_S, a standard flag.
    """
    r_size = sig[3]
    r = sig[4 : 4 + r_size]
    high = _N - int.from_bytes(sig[6 + r_size : -1], "big")
    s = high.to_bytes(high.bit_length() // 8 + 1, "big")
    body = b"\x02" + bytes([r_size]) + r + b"\x02" + bytes([len(s)]) + s
    return b"\x30" + bytes([len(body)]) + body + sig[-1:]


def test_sign_refuses_a_high_s_signature(tmp_path: Path) -> None:
    """Bob's signature with a high S verifies under consensus, not policy.

    Core's signer verifies under STANDARD_SCRIPT_VERIFY_FLAGS, LOW_S
    among them, and refuses it.
    """
    given = _Signed(tmp_path)
    given.tx.vin[1].script_witness = Witness([_high_s(given.sig), given.pub_key])
    verify_input(given.prevouts, given.tx, 1)
    with pytest.raises(ValueError, match="^inputs left unsigned$"):
        given.alice.sign(given.tx)
    given.factory.close()


def test_sign_refuses_a_signed_input_whose_coin_is_spent(tmp_path: Path) -> None:
    """Core answers `Input not found or already spent`: a block spent it."""
    given = _Signed(tmp_path)
    given.chain.mine(given.spend_bob_s_coin())
    with pytest.raises(ValueError, match="^inputs left unsigned$"):
        given.alice.sign(given.tx)
    given.factory.close()


def test_sign_refuses_an_input_another_party_left_unsigned(tmp_path: Path) -> None:
    """Bob's coin is unspent, and nobody signed it."""
    given = _Signed(tmp_path)
    given.tx.vin[1].script_witness = Witness()
    with pytest.raises(ValueError, match="^inputs left unsigned$"):
        given.alice.sign(given.tx)
    given.factory.close()


def test_sign_refuses_an_output_its_transaction_does_not_have(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """The wallet's payment has one output: its second is nobody's coin."""
    wallet, _, payment = funded
    out = TxOut(1_000, ScriptPubKey.from_address(_ADDRESS))
    tx = Tx(2, 0, [TxIn(OutPoint(payment.id, 1))], [out])
    with pytest.raises(ValueError, match="^inputs left unsigned$"):
        wallet.sign(tx)


def test_sign_signs_again_the_wallet_s_input_a_bad_witness_carries(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """The witness the caller gave is replaced: Core signs the input again."""
    wallet, _, payment = funded
    bad = Witness([b"\x30" * 71, b"\x02" * 33])
    out = TxOut(_VALUE // 2, ScriptPubKey.from_address(_ADDRESS))
    tx = Tx(2, 0, [TxIn(OutPoint(payment.id, 0), script_witness=bad)], [out])
    signed = wallet.sign(tx)
    assert signed.vin[0].script_witness != bad
    verify_input([payment.vout[0]], signed, 0)


def test_sign_keeps_the_wallet_s_input_a_valid_witness_carries(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """Signed with ANYONECANPAY, it is kept: Core keeps a complete input.

    The ledger signs with SIGHASH_ALL, so a witness signed again would
    differ from this one.
    """
    wallet, chain = made
    xprv = rootxprv_from_seed(bytes(range(32)), NETWORKS["regtest"].bip32_prv)
    wallet.import_descriptor(add_checksum(f"wpkh({xprv}/0/*)"))
    coin = _payment(wallet.ledger.new_address(1))
    chain.mine(coin)
    out = TxOut(_VALUE // 2, ScriptPubKey.from_address(_ADDRESS))
    wallet.balance()
    psbt = wallet.ledger.sign(
        Psbt.from_tx(Tx(2, 0, [TxIn(OutPoint(coin.id, 0))], [out]))
    )
    anyone_can_pay: Literal[0x81] = 0x81  # SIGHASH_ALL | SIGHASH_ANYONECANPAY
    psbt.inputs[0].partial_sigs = {}
    psbt.inputs[0].sig_hash_type = anyone_can_pay
    psbt = SoftwareSigner(xprv).sign_psbt(psbt, allowed_sig_hash_types={anyone_can_pay})
    given = extract_tx(finalize(psbt), max_fee_rate=None)
    assert given.vin[0].script_witness.stack[0][-1] == anyone_can_pay
    signed = wallet.sign(given)
    assert signed.vin[0].script_witness == given.vin[0].script_witness


def test_sign_checks_no_amount(funded: tuple[BtclibWallet, _Chain, Tx]) -> None:
    """Outputs above the inputs are signed: Core's wallet checks no amount."""
    wallet, _, payment = funded
    out = TxOut(2 * _VALUE, ScriptPubKey.from_address(_ADDRESS))
    signed = wallet.sign(Tx(2, 0, [TxIn(OutPoint(payment.id, 0))], [out]))
    verify_input([payment.vout[0]], signed, 0)


def test_sign_keeps_a_pay_to_anchor_input_s_empty_scripts(
    funded: tuple[BtclibWallet, _Chain, Tx],
) -> None:
    """A P2A input verifies with no `script_sig` and no witness.

    Core's `SignStep` solves `TxoutType::ANCHOR` with nothing, and its
    interpreter accepts P2A under the standard flags.
    """
    wallet, chain, payment = funded
    anchor = Tx(
        2,
        0,
        [TxIn(OutPoint(b"\x02" * 32, 0))],
        [TxOut(240, ScriptPubKey(PAY_TO_ANCHOR, "regtest"))],
    )
    chain.mine(anchor)
    vin = [TxIn(OutPoint(payment.id, 0)), TxIn(OutPoint(anchor.id, 0))]
    out = TxOut(_VALUE // 2, ScriptPubKey.from_address(_ADDRESS))
    signed = wallet.sign(Tx(2, 0, vin, [out]))
    assert not signed.vin[1].script_sig
    assert not signed.vin[1].script_witness.stack
    prevouts = [payment.vout[0], anchor.vout[0]]
    verify_input(prevouts, signed, 0)
    verify_input(prevouts, signed, 1)


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


def test_an_import_the_locked_ledger_refuses_names_its_type_alone(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """A descriptor that parses, refused by `Ledger.add_source`.

    An encrypted ledger whose keys are locked refuses a source holding a
    private key.
    """
    wallet, _ = made
    xprv = rootxprv_from_seed(bytes(range(32)), NETWORKS["regtest"].bip32_prv)
    wallet.ledger.encrypt("passphrase")
    wallet.ledger.lock_keys()
    with pytest.raises(
        ValueError, match="^descriptor not imported: BTClibValueError$"
    ) as excinfo:
        wallet.import_descriptor(add_checksum(f"wpkh({xprv}/0/*)"))
    _assert_secret_free(excinfo.value, xprv)


def test_a_refusal_of_add_source_quoting_the_key_is_not_relayed(
    made: tuple[BtclibWallet, _Chain], monkeypatch: pytest.MonkeyPatch
) -> None:
    """What `Ledger.add_source` says of the descriptor is named by type."""
    wallet, _ = made
    descriptor = add_checksum(f"wpkh({_WIF})")
    refusal = BTClibValueError(f"not added: {descriptor}")
    monkeypatch.setattr(Ledger, "add_source", _refused_source(refusal))
    with pytest.raises(
        ValueError, match="^descriptor not imported: BTClibValueError$"
    ) as excinfo:
        wallet.import_descriptor(descriptor)
    _assert_secret_free(excinfo.value, _WIF)


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
    """The refusal names the state and not the id, and chains nothing."""
    wallet, _, payment = funded
    with pytest.raises(
        ValueError, match="^not abandoned: a confirmed transaction$"
    ) as excinfo:
        wallet.abandon(payment.id)
    _assert_secret_free(excinfo.value, payment.id)


def test_unload_reads_the_node_then_load_answers_from_the_file(
    made: tuple[BtclibWallet, _Chain],
) -> None:
    """A payment in the mempool at the unload is still the wallet's after it."""
    wallet, chain = made
    payment = _payment(wallet.new_address())
    chain.mempool.append(payment)
    wallet.unload()
    with pytest.raises(ValueError, match="^the wallet is unloaded$"):
        wallet.balance()
    chain.mempool = []
    wallet.load()
    assert wallet.transaction(payment.id).confirmations == 0


def test_an_unloaded_wallet_named_with_a_key_does_not_quote_it(
    tmp_path: Path,
) -> None:
    """The refusal leaves out the name, which can be a key given by mistake."""
    chain = _Chain()
    factory = BtclibWalletFactory(tmp_path, lambda _: chain)
    wallet = factory.create(cast("NodeAdapter", _Node()), _WIF)
    wallet.unload()
    with pytest.raises(ValueError, match="^the wallet is unloaded$") as excinfo:
        _ = wallet.ledger
    _assert_secret_free(excinfo.value, _WIF)
    factory.close()


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


class _CoinNode:
    """A node answering `NodeBackend.get_coin_parent`, keeping each call.

    `by_id` is what `getrawtransaction` answers without a block: a
    verbose answer, or the `RpcError` it raises. With a block it answers
    `in_block`, `parent`'s hex unless a test sets another hex or an
    `RpcError`.
    """

    chain = "regtest"

    def __init__(self, parent: Tx, coin: object = None, by_id: object = None) -> None:
        self.answers: dict[str, object] = {
            "gettxout": coin,
            "getblockheader": {"height": 7},
            "getblockhash": "bb" * 32,
            "getbestblockhash": "aa" * 32,
        }
        self.by_id = by_id or RpcError("No such mempool transaction", -5)
        self.in_block: object = _hex(parent)
        self.calls: list[tuple[str, list[object]]] = []

    @property
    def rpc(self) -> _CoinNode:
        return self

    def call(self, method: str, params: list[object]) -> object:
        self.calls.append((method, params))
        if method != "getrawtransaction":
            return self.answers[method]
        if len(params) == 3:
            if isinstance(self.in_block, RpcError):
                raise self.in_block
            return self.in_block
        if isinstance(self.by_id, RpcError):
            raise self.by_id
        return self.by_id


def _hex(tx: Tx) -> str:
    """Return `tx`'s serialization, witness included, as hex."""
    return tx.serialize(include_witness=True).hex()


def _coin_parent(node: _CoinNode, outpoint: OutPoint) -> Tx | None:
    """Return `NodeBackend.get_coin_parent`'s answer over `node`."""
    return NodeBackend(cast("NodeAdapter", node)).get_coin_parent(outpoint)


_COIN = {"confirmations": 2, "bestblock": "aa" * 32}


def test_node_backend_reads_a_chain_coin_s_transaction_in_its_block() -> None:
    """The block is the one the coin's confirmations name: no `-txindex`.

    `gettxout` leaves the mempool out, where a spend would hide the coin.
    """
    parent = _payment(_ADDRESS)
    txid = parent.id.hex()
    node = _CoinNode(parent, _COIN)
    found = _coin_parent(node, OutPoint(parent.id, 0))
    assert found is not None
    assert found.id == parent.id
    assert found.vout[0].script_pub_key.network == "regtest"
    assert node.calls == [
        ("getrawtransaction", [txid, True]),
        ("gettxout", [txid, 0, False]),
        ("getblockheader", ["aa" * 32]),
        ("getblockhash", [6]),
        ("getbestblockhash", []),
        ("getrawtransaction", [txid, False, "bb" * 32]),
    ]


def test_node_backend_reads_a_mempool_transaction_by_its_id() -> None:
    """Each of its outputs is a coin, spent in the mempool or not."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent, by_id={"hex": _hex(parent)})
    found = _coin_parent(node, OutPoint(parent.id, 0))
    assert found is not None
    assert found.id == parent.id
    assert node.calls == [("getrawtransaction", [parent.id.hex(), True])]


@pytest.mark.parametrize("coin", [None, _COIN], ids=["spent", "unspent"])
def test_node_backend_reads_the_chain_for_a_transaction_txindex_finds(
    coin: dict[str, object] | None,
) -> None:
    """An answer naming its block is checked in the chain, which decides.

    `getrawtransaction` reads `-txindex` once the mempool lacks the
    transaction: mined, perhaps in the block of a child spending this
    output.
    """
    parent = _payment(_ADDRESS)
    txid = parent.id.hex()
    by_id = {"hex": _hex(parent), "blockhash": "cc" * 32}
    node = _CoinNode(parent, coin, by_id)
    found = _coin_parent(node, OutPoint(parent.id, 0))
    assert (found is None) == (coin is None)
    assert node.calls[:2] == [
        ("getrawtransaction", [txid, True]),
        ("gettxout", [txid, 0, False]),
    ]


def test_node_backend_refuses_another_transaction_by_its_id() -> None:
    """The mempool's answer is the transaction asked for, or refused."""
    parent = _payment(_ADDRESS)
    other = _payment(_ADDRESS, _VALUE + 1)
    node = _CoinNode(parent, by_id={"hex": _hex(other)})
    with pytest.raises(FetchError, match="not the one asked"):
        _coin_parent(node, OutPoint(parent.id, 0))


def test_node_backend_refuses_another_transaction_in_the_block() -> None:
    """The block's answer is the transaction asked for, or refused."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent, _COIN)
    node.in_block = _hex(_payment(_ADDRESS, _VALUE + 1))
    with pytest.raises(FetchError, match="not the one asked"):
        _coin_parent(node, OutPoint(parent.id, 0))


def test_node_backend_refuses_a_block_found_while_the_tip_moved() -> None:
    """The height `gettxout`'s tip names may be another chain's block."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent, _COIN)
    node.answers["getbestblockhash"] = "dd" * 32
    with pytest.raises(FetchError, match="the tip moved"):
        _coin_parent(node, OutPoint(parent.id, 0))
    assert node.calls[-1] == ("getbestblockhash", [])


def test_node_backend_refuses_a_coin_whose_block_is_pruned() -> None:
    """`gettxout` holds the coin, and the node no longer the block."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent, _COIN)
    node.in_block = RpcError("Block not available", -1)
    with pytest.raises(FetchError, match="pruned"):
        _coin_parent(node, OutPoint(parent.id, 0))


def test_node_backend_raises_any_other_error_reading_the_block() -> None:
    """Only `RPC_MISC_ERROR` says the block is not available."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent, _COIN)
    node.in_block = RpcError("No such transaction found in the provided block", -5)
    with pytest.raises(RpcError, match="provided block"):
        _coin_parent(node, OutPoint(parent.id, 0))


def test_node_backend_answers_none_for_an_output_a_mempool_tx_lacks() -> None:
    """`CCoinsViewMemPool::GetCoin` answers no coin past the last output."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent, by_id={"hex": _hex(parent)})
    assert _coin_parent(node, OutPoint(parent.id, 1)) is None


def test_node_backend_answers_none_for_a_coin_the_node_does_not_hold() -> None:
    """`gettxout` answers null for a coin spent in the chain, or unknown."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent)
    assert _coin_parent(node, OutPoint(parent.id, 0)) is None
    txid = parent.id.hex()
    assert node.calls == [
        ("getrawtransaction", [txid, True]),
        ("gettxout", [txid, 0, False]),
    ]


def test_node_backend_raises_any_other_rpc_error() -> None:
    """Only `RPC_INVALID_ADDRESS_OR_KEY` says the node lacks it."""
    parent = _payment(_ADDRESS)
    node = _CoinNode(parent, by_id=RpcError("warming up", -28))
    with pytest.raises(RpcError, match="warming up"):
        _coin_parent(node, OutPoint(parent.id, 0))
