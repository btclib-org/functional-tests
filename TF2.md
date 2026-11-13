# The tf2 ledger

One entry per Python file of Bitcoin Core's
`test/functional/test_framework/`, naming what covers it in `btclib`,
what the covering module asserts, and the revision of that file the
entry was read at.

What it is for is
[ISS 198](https://github.com/btclib-org/btclib/issues/198)'s "strictly
equivalent", the phrase on which offering anything back to Core rests.
That is a claim about a moving target: Core's framework changes, so an
equivalence measured against a revision nobody wrote down is an
equivalence nobody can re-check. An entry here turns "btclib covers
`blocktools`" into a sentence with a truth value -- which `blocktools`,
asserted by what, and what has moved upstream since.

It makes the gap visible in the other direction too. Re-deriving what is
left of Core's framework means reading the directory again, which is
work that starts going stale the moment it is finished; this is that
reading written down once, and moved by whatever moves it.

A verdict names btclib's covering module wherever btclib covers the
file, and this repository's own module or test wherever this repository
does; `CONTRIBUTING.md`'s *The public surface* names the modules it
holds.

## Reading an entry

An entry is a `###` heading naming the file's path in Core, a fenced
block pinning it, and a verdict with what stands behind it.

`repo`, `path` and `commit` are the pin, and `behind` is how many
upstream revisions of that path have landed since. That is the grammar
`.github/scripts/check_vendored_vectors.py` parses, which
`.github/workflows/vendored-vectors.yml` runs over this file weekly: a
pin here that has moved says a verdict is now a claim about a file that
has moved.

An entry's `commit` is the tip of the path the entry names, so `behind`
reads zero and a re-check can clear it. A repository-wide revision is a
perfectly good "this is the tree I read" pin, and `contents/<path>?ref=<sha>`
resolves one; what it settles is the tree and not the path. It is the
tip of a path only where nothing on the default branch has touched that
path since, which is a fact about that history rather than about the
pin, so a per-path `behind` cannot be counted on to read zero for such a
revision, and a ledger whose whole purpose is re-checkability takes the
per-path pin, which is the one that goes stale loudly.

## The verdicts

- **covered** -- btclib publishes the same thing, and a test asserts it.
- **covered in part** -- some of the file's surface is btclib's and some
  is not; the entry names both halves.
- **vendored** -- btclib holds Core's own file, with the attribution.
- **tf2's by decision** -- an issue decided it out of btclib, and the
  entry names that issue.
- **tf2's (harness)** -- it drives a node: a process, an RPC client, a
  socket, an event loop. Nothing in btclib answers it, and nothing is
  meant to; this repository's own adapters and harness are where it
  lands, and an entry this repository already covers names what does.
- **empty upstream** -- the file has no content at the pinned commit.
  The entry is there so that content arriving in it moves the pin.

## Re-checking a pin

The path stands in a fence of its own, sitting inside the API argument
rather than at the end of the command; the fence below reads it as
`${entry_path:?}`, the shell's must-be-set form, so a paste of that
fence alone fails naming the variable rather than asking about whatever
the shell already held.

The variable is `entry_path` and not `path`, which is the name the
argument wants: in `zsh` -- the shell this is read in -- `path` is tied
to `PATH` as an array, so assigning a file path to it replaces the
reader's `PATH` with that one entry and the next command in their
terminal is not found.

```shell
entry_path=<the path the entry gives>
```

```shell
gh api --method GET repos/bitcoin/bitcoin/commits \
    -f "path=${entry_path:?}" -f per_page=1 \
    --jq '.[0].sha + "  " + .[0].commit.committer.date[:10]'
```

The answer is the entry's own `commit` where nothing has moved. Any
other commit means the file changed after it was read, and the entry's
verdict is then a claim about a file that is no longer there.

**What a per-pin re-check cannot catch is a file Core gains.**
`tests/tf2_ledger_test.py` runs offline and holds this file to a
transcription of Core's directory rather than to Core, so a new
`test_framework/*.py` upstream is invisible to it and to the pin
re-check above, which reads only the entries a ledger already carries
([ISS btclib-org/btclib#1751](https://github.com/btclib-org/btclib/issues/1751)).
`.github/scripts/tf2_ledger_census.py` is the read that answers it: a
recursive listing of the directory at a fresh commit, compared against
this file's own entries in both directions, with `.truncated` checked
rather than assumed and the `contents` endpoint never used -- it lists a
directory non-recursively, so it would answer without `crypto/*.py` and
report every file one directory down as gone. `vendored-vectors.yml`
runs it weekly, under a title distinct from the per-pin drift issue, so
a gained file and a stale pin are never the same issue.

No entry carries a `blob`, which is where this file parts from
`tests/_data/README.md`: nothing in this tree is a copy of any file
below but the RIPEMD-160 implementation, whose own entry names where
that comparison lives instead of making one here.

## Citing the framework from btclib and from tf2

A module citing one of these files names the file and the *function*,
carrying no revision of its own and no line number: an edit upstream
moves a line with nothing going red, so what a citation names beside the
file is the function. `script/sig_hash.py`'s `segwit_v0` cites
`SegwitV0SignatureHash` of `test/functional/test_framework/script.py`.

**A citation of Core's C++ is a different thing**, and none of the above
reaches it. This file pins no C++ path, so such a citation has no entry
here to leave a revision to.

**A vendoring line is not a citation.** `_ripemd160.py` is Core's own
file, and the revision its docstring carries says which bytes were
copied, beside the attribution the licence asks for. That pin belongs
where the copy is, and moves when the copy does -- it stays in `btclib`,
not here, since the copy is there.

## bitcoin/bitcoin

### `test/functional/test_framework/__init__.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/__init__.py
commit  c28ee91db07ce82e134d500ddeb5600363c98048  2017-03-20
behind  0 revisions; that commit is the tip of the path
```

Verdict: **empty upstream**. The package marker, with no content at the
pinned commit. The entry is here so that content arriving in it moves
the pin rather than passing unnoticed.

### `test/functional/test_framework/address.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/address.py
commit  4dbaa7cc65b9546d07f1f9bfc8fb912b6fb20a5e  2026-06-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `b58.address_from_h160` and `b58.h160_from_address`
for the base58 addresses, `b32.address_from_witness` and
`b32.witness_from_address` for the segwit ones, each over the codec
below it -- `base58-bech32`'s `base58` and `bech32`, a split Core's file
does not make.
`script.script_pub_key`'s `address`, `addresses` and `type_and_payload`
are the `address_to_scriptpubkey` direction.
`create_deterministic_address_bcrt1_p2tr_op_true` composes
`script.taproot.output_pubkey` over `tree_helper` with `b32.p2tr`, so it
is a convenience of tf2's assembled from parts btclib already has.

### `test/functional/test_framework/authproxy.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/authproxy.py
commit  f42226d526ebb3eb9217e738108e4f8148a1b069  2026-06-04
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. Reaching a node over JSON-RPC is the
`bitcoin-core-rpc` package, which this repository depends on directly
and which `btclib.fetch.bitcoin_core` also builds on. Core's file is not
a vendoring candidate either: its header puts it under the GNU Lesser
General Public License, which is not this project's.

### `test/functional/test_framework/blockfilter.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/blockfilter.py
commit  fa5f29774872d18febc0df38831a6e45f3de69cc  2025-12-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `block/block_filter.py` holds both halves.
`BasicBlockFilter` maps an element into the filter's range as
`bip158_basic_element_hash` does, keyed on the block hash, and its
`from_block` selects what `bip158_relevant_scriptpubkeys` selects --
the previous output script of every non-coinbase input, and every output
script BIP158 does not exclude -- with the utxo set handed in through
`prevout_scripts_from_utxos` rather than read off a node. Each draws the
output rule from a different place, and btclib's is the one the BIP
states: Core's Python asks whether the output type is `nulldata`, where
btclib tests the first byte for `OP_RETURN` as Core's own C++ does.
`p2p/block_filters.py` carries BIP157's messages over it.

### `test/functional/test_framework/blocktools.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/blocktools.py
commit  1966621b76885257b4b4e44aab4712e9f84313e6  2026-05-13
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `block/build.py` is `create_coinbase`,
`create_block` and `add_witness_commitment`:
[ISS 1118](https://github.com/btclib-org/btclib/issues/1118) put
`build_coinbase`, which pays `consensus.subsidy` at a height and commits
to that height as BIP34 requires, beside `build_block`, which assembles
the block over a list of transactions and its witness commitment.
`block/proof_of_work`'s `target_from_bits` and `bits_from_target` are
what `nbits_str` and `target_str` print, and `script.sig_ops`'s
`sig_op_count` is what `get_legacy_sigopcount_tx` counts.
`create_tx_with_script`, `create_witness_tx` and `send_to_witness` spend
against a node's wallet, and are tf2's.

### `test/functional/test_framework/compressor.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/compressor.py
commit  b34fdb5ade0b48384636f8c7c9673554bf82cedf  2025-03-04
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's by decision**.
[ISS 1123](https://github.com/btclib-org/btclib/issues/1123): the
chainstate encoding is the format Core reserves for itself, where
`fetch/` depends on the interface Core publishes for everyone else.
`util.util_xor`, the obfuscation of Core's own block files, went to tf2
with it.

### `test/functional/test_framework/coverage.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/coverage.py
commit  fa71c15f8610816a6ee0426cd396315da3d27c30  2025-11-26
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. It records which RPCs a run reached.
Nothing in btclib answers it.

### `test/functional/test_framework/crypto/bip324_cipher.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/bip324_cipher.py
commit  fa5f29774872d18febc0df38831a6e45f3de69cc  2025-12-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `p2p/bip324.py`: `FSChaCha20Poly1305` is
Core's class of that name, rekeying every `REKEY_INTERVAL` packets, and
`Cipher.encrypt` and `Cipher.decrypt` run it over a packet.
btclib's `tests/p2p/bip324_test.py` drives it with BIP324's packet
vectors. The module needs btclib's `bip324` extra.

What Core writes out by hand -- `pad16` and
`aead_chacha20_poly1305_encrypt` and `_decrypt` -- is not btclib's.
[ISS 1066](https://github.com/btclib-org/btclib/issues/1066) drew the
line: "What btclib can build out of the Python standard library is in
scope. What would need a hand-rolled cipher is not: that is tf2's." The
`bip324` extra brings in `cryptography`, and `bip324.py` uses its
`ChaCha20Poly1305` for the construction
([ISS btclib-org/btclib#2474](https://github.com/btclib-org/btclib/issues/2474),
landed as PR btclib-org/btclib#2516).
`peer.py` reaches the construction through btclib's `Cipher`.

### `test/functional/test_framework/crypto/chacha20.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/chacha20.py
commit  fa5f29774872d18febc0df38831a6e45f3de69cc  2025-12-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `p2p/bip324.py`: `FSChaCha20` is Core's
class of that name, the cipher of the packet lengths, over
`cryptography`'s ChaCha20, and `bip324_test.py` drives it with BIP324's
packet vectors. `chacha20_block`, `chacha20_doubleround` and `rotl32`
are the hand-written cipher that ISS btclib-org/btclib#1066 keeps out of
btclib. `muhash.py` carries a private `_chacha20_block` because
`MuHash3072`'s element hash is a keyed ChaCha20 keystream and nothing
else in btclib needs one; its `__all__` publishes `MuHash3072` alone.

### `test/functional/test_framework/crypto/ellswift.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/ellswift.py
commit  3fd68a95e68b4c6f3bb6c59d41dd196001110f3a  2026-04-07
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `ecc`'s `ellswift.py`: `create` for
`ellswift_create`, `encode_var` for `xelligatorswift` and `decode_var`
for `xswiftec`, over `_xswiftec_var` and `_xswiftec_inv_var`.
`ellswift_ecdh_xonly` returns the bare x-coordinate of the shared point:
it is `decode_var` and a point multiplication, a composition of tf2's
from parts btclib already has. btclib's `xdh` is the tagged hash that
`v2_p2p.py`'s `v2_ecdh` takes of both encodings and that x-coordinate.

### `test/functional/test_framework/crypto/hkdf.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/hkdf.py
commit  fa5f29774872d18febc0df38831a6e45f3de69cc  2025-12-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `kdf.hkdf` is Core's `hkdf_sha256`, and
`kdf.hkdf_extract` and `kdf.hkdf_expand` stand beside it where Core's
file offers the one-shot alone
([ISS 1080](https://github.com/btclib-org/btclib/issues/1080)).

### `test/functional/test_framework/crypto/muhash.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/muhash.py
commit  fec2ca6c9a8a8e44b6e4d51c4ef7fa6eaca6e446  2023-09-29
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `muhash.MuHash3072` is Core's class operation for
operation -- `insert`, `remove` and the digest are all the pinned file
has -- and `coinstats.py` is what feeds a utxo set through one. btclib's
`serialize` and `deserialize` answer Core's C++ class instead, which the
module says where it implements them.

### `test/functional/test_framework/crypto/poly1305.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/poly1305.py
commit  fa5f29774872d18febc0df38831a6e45f3de69cc  2025-12-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's by decision**. ISS btclib-org/btclib#1066, the same
line: an authenticator written out by hand is what that issue put on
tf2's side. btclib publishes no Poly1305 of its own.
`bip324_cipher.py` imports `Poly1305` for its hand-written AEAD, which
`bip324.FSChaCha20Poly1305` replaces, so a test that wants a packet's
tag asks that class and not this file.

### `test/functional/test_framework/crypto/ripemd160.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/ripemd160.py
commit  08a4a56cbcfa54366c2c0bb52bb147fc2740edc5  2023-09-10
behind  0 revisions; that commit is the tip of the path
```

Verdict: **vendored**. `btclib`'s `src/btclib/_ripemd160.py` is Core's
file, MIT and therefore that project's licence too, for an interpreter
whose `hashlib` offers no RIPEMD-160; its docstring carries the
attribution, the revision it was taken at, and every delta from
upstream. That vendoring lives in `btclib`, not here.

### `test/functional/test_framework/crypto/secp256k1.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/secp256k1.py
commit  b6fc3cf0046d3106e0dfb30f320d48514eff6fb2  2026-09-28
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**, and wider than Core's file: `ecc`'s
`curves/curve.py`, `curves/curve_group.py`, `curves/curve_group_2.py`,
`curves/curve_group_f.py` and `curves/sec_point.py` carry every curve
it has rather than secp256k1 alone. What the arithmetic is asserted
against is not a vector file but the `btclib_secp256k1` bindings, which
`curves.curve.mult` and its variants delegate to for secp256k1 and which
`ecc`'s own suite validates the Python arm against. Core's file
warns that it is slow and side-channel vulnerable; `ecc`'s
`SECURITY.md` publishes the same about its Python arm.

### `test/functional/test_framework/crypto/siphash.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/crypto/siphash.py
commit  af50ba8500a81e1a79cdf953d27bbf53ee6c979f  2026-07-17
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `hashes.siphash` takes the key words in the order
Core's own `siphash(k0, k1, data)` reads them out of a key
([ISS 373](https://github.com/btclib-org/btclib/issues/373)). Core's
second entry point, `siphash256`, is a little-endian `to_bytes` of a
`uint256` ahead of that same call, and `hashes.siphash`'s docstring is
where declining to restate that conversion is argued: a hash is already
bytes in that order in btclib.

### `test/functional/test_framework/descriptors.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/descriptors.py
commit  fab300b378941a233119805c0d62198596a57790  2025-12-26
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `descriptors.checksum`,
`descriptors.add_checksum` and `descriptors.strip_checksum` are
`descsum_polymod`, `descsum_expand`, `descsum_create` and
`descsum_check`. `drop_origins` has no equivalent: `descriptors.normalized`
is Core's `ToNormalizedString`, which re-roots each key at its last
hardened step, where dropping key origins is a different operation btclib
does not publish.

### `test/functional/test_framework/extendedkey.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/extendedkey.py
commit  d2a03d50acbf4bffc11048ef2cabb1b42ea78989  2026-06-19
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `bip32/` is BIP32 whole, where Core's file says of
itself that it provides "only basic functionality", and `slip132`,
`bip44`, `bip85` and `bip32/key_origin.py` stand beside it.

### `test/functional/test_framework/ipc_util.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/ipc_util.py
commit  858814146dd2ee17b2dda8ad01f50257affa23f5  2026-08-28
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. Cap'n Proto IPC into a `bitcoin-node`
process. `btclib` names it nowhere.

### `test/functional/test_framework/key.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/key.py
commit  00d0c5107b92c8972ab106c680579e06fd4fb100  2026-09-09
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `ecc`'s `dsa.py` and `ssa.py` sign and verify,
its `rfc6979_nonce.py` and `bip340_nonce.py` derive the nonces, and its
`hashes.tagged_hash` is `TaggedHash`. btclib's `key.py` holds the key
objects Core's `ECKey` and `ECPubKey` are. What the signatures are
asserted against is BIP340's own vectors and the `btclib_secp256k1`
bindings.

### `test/functional/test_framework/mempool_util.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/mempool_util.py
commit  fa5f29774872d18febc0df38831a6e45f3de69cc  2025-12-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. `fill_mempool` is ported, in
`src/btclib_functional_tests/mempool_util.py`, over a throwaway `MiniWallet`
from `mini_wallet.py`
([ISS functional-tests#70](https://github.com/btclib-org/functional-tests/issues/70)).
`tx_in_orphanage` is `tests/integration/rpc_orphans_test.py`'s own
`in_orphanage`, and `create_large_orphan` is
`tests/integration/p2p_orphan_handling_test.py`'s own `_large_orphan`,
and `assert_mempool_contents` is
`tests/integration/mempool_package_rbf_test.py`'s own
`_assert_mempool_contents`.

### `test/functional/test_framework/messages.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/messages.py
commit  e3b026bf56e66baa6e070a00c136da95f3a16ef8  2026-07-07
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `p2p/` publishes one `Payload` subclass
per command over `tx/`, `block/`, `var_int`, `var_bytes`, `hashes`,
`block/partial_merkle_tree.py` and `p2p/merkleblock.py`; the
`C`-prefixed structures Core's file also carries are `tx/` and `block/`
in btclib. `filterload`, `filteradd` and `filterclear` are left out for
good by [ISS 1120](https://github.com/btclib-org/btclib/issues/1120): a
client constructs those to ask a node for the service that leaks its
wallet to that node, and BIP157 is what replaced them.

### `test/functional/test_framework/netutil.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/netutil.py
commit  0cd0789c86b39ba0ae4670068d5be351cdcf92ae  2026-10-06
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. Interfaces, sockets and kernel inode
tables. `btclib` imports `socket` nowhere.

### `test/functional/test_framework/p2p.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/p2p.py
commit  8dc80cafa2ee5932c9ff499c02d6ab8e3cf2f5ef  2026-10-08
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. The envelope is `p2p/message.py`:
`Message.parse` reads one message off a stream and answers "not enough
bytes yet" as `IncompleteMessageError`, distinctly from a wrong
checksum, rewinding the stream to where it started.
`P2PConnection`, `P2PInterface`, `NetworkThread`, `P2PDataStore` and the
listener are the socket, the event loop and the peer state machine, and
are tf2's.

### `test/functional/test_framework/psbt.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/psbt.py
commit  a2a2b1745f0818364dd8149161e88cea1475d9b6  2026-05-27
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `psbt/` -- `Psbt`, `PsbtIn`, `PsbtOut`,
`psbt_view`, `psbt_size`, `musig2` and `silent_payments` -- where Core's
file is a reader and a writer of the maps.

### `test/functional/test_framework/script.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/script.py
commit  3fd68a95e68b4c6f3bb6c59d41dd196001110f3a  2026-04-07
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `script/script.py` is the codec,
`script/engine/` the execution, `script/op_codes_tapscript.py` and
`script/taproot.py` the taproot half, and `script/sig_hash.py` the
signature hashes Core's file also carries.

### `test/functional/test_framework/script_util.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/script_util.py
commit  3fd68a95e68b4c6f3bb6c59d41dd196001110f3a  2026-04-07
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `script/script_pub_key.py` publishes the
`ScriptPubKey` constructors Core's `*_script` functions are, with the
`assert_*` and `is_*` pair beside them, `type_and_payload` and
`address`. `bulk_vout` and `build_malleated_tx_package` build a
transaction of a chosen size or a package of a chosen shape for a node
to accept or refuse, and are tf2's.

### `test/functional/test_framework/segwit_addr.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/segwit_addr.py
commit  fe5e495c31de47b0ec732b943db11fe345d874af  2021-03-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `base58-bech32`'s `bech32.py` is the codec with no
bitcoin in it and btclib's `b32.py` the bitcoin semantics on top, which
is the split this file does not make.

### `test/functional/test_framework/signet.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/signet.py
commit  9b37d42b23be096cc4cfb457f1022e443102b650  2026-09-11
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered**. `message_start` is BIP325's rule for a signet's
p2p magic: the challenge script, serialized with its CompactSize length,
SHA256d, then truncated to the magic's own width.
`bitcoin_core_rpc.magic_from_signet_challenge` is that same derivation,
and its own docstring states the rule in the same words Core's function
implements. `DEFAULT_SIGNET_CHALLENGE` there is Core's own
`SIGNET_DEFAULT_CHALLENGE` -- both constants compare equal, byte for
byte -- and `magic_from_chain("signet")` is the constant table entry
`SigNetParams` in Core's `chainparams.cpp` sets from exactly this
derivation, so the constant and the derivation are cross-checked against
each other rather than one merely standing in for the other.

This file's first and only revision is 2026-09-11
([ISS btclib-org/btclib#1751](https://github.com/btclib-org/btclib/issues/1751)'s
finding): the directory read this ledger's move brings from the start is
what would have caught it, over the days between its landing upstream
and this ledger's own move.

### `test/functional/test_framework/socks5.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/socks5.py
commit  4e8c4bc794c045beb678854e6e326fc04322c7c3  2026-08-04
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. A SOCKS5 server for the Tor tests.
`btclib` names SOCKS nowhere; `socks5.py`'s `Socks5Proxy` is this
repository's.

### `test/functional/test_framework/test_framework.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/test_framework.py
commit  b469ff8fddb6594b69d564cca436b7baa8ed99bb  2026-10-23
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. The base class every functional test
derives from.

### `test/functional/test_framework/test_node.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/test_node.py
commit  276db20b64e841fda73cf074501043b88af963ca  2026-10-19
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. It starts, stops and drives a `bitcoind`,
and wraps `bitcoin-cli`.

### `test/functional/test_framework/test_shell.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/test_shell.py
commit  fa5f29774872d18febc0df38831a6e45f3de69cc  2025-12-16
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. The framework driven from a python shell.

### `test/functional/test_framework/util.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/util.py
commit  8dc80cafa2ee5932c9ff499c02d6ab8e3cf2f5ef  2026-10-08
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `fee.fee_from_vsize` over a `FeeRate` is
what `get_fee` computes, and what `satoshi_round` quantizes to is what
`amount.py`'s `valid_btc_amount`, `sats_from_btc` and `btc_from_sats`
work in; `util_xor` went to tf2 with the compressor by
ISS btclib-org/btclib#1123. The rest is the harness: the assertions,
the ports, the datadirs, the cookie files, the configuration files and
the waits.

### `test/functional/test_framework/v2_p2p.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/v2_p2p.py
commit  6a129983c9bf8efa1081f9a8b462c3635d1cfb39  2026-06-04
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `EncryptedP2PState`'s keys and packets are
`p2p/bip324.py`'s `Cipher`. Its constructor is `v2_ecdh`
(btclib's `ellswift.xdh`) followed by `initialize_v2_transport`
(`kdf.hkdf_extract` and `kdf.hkdf_expand`). `v2_enc_packet` and
`v2_receive_packet` are `Cipher.encrypt`, `Cipher.decrypt_length` and
`Cipher.decrypt`. `SHORTID` and `MSGTYPE_TO_SHORTID` are
`MESSAGE_IDS`, with `contents_from_message` and `message_from_contents`.

The handshake is tf2's, and `peer.py`'s `Peer` holds it: the keypair and
garbage each side sends, sending the garbage terminator and finding the
peer's (`Cipher` derives both), the decoy packets, the version packet,
the v1 `version` a responder reads first, and `MAX_GARBAGE_LEN`.
`bip324.py` leaves them to its caller.

### `test/functional/test_framework/wallet.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/wallet.py
commit  91586f701e1ebb12d8147feda7e78169a05da36f  2026-07-01
behind  0 revisions; that commit is the tip of the path
```

Verdict: **tf2's (harness)**. `MiniWallet` spends against a node's
chain. Its building blocks are `btclib-wallet`'s -- `tx_builder.build_psbt`,
`coin_selection`, `psbt_signer` and `btclib`'s `script/script_pub_key.py`
-- so it is tf2 written on those packages rather than a gap in either.

### `test/functional/test_framework/wallet_util.py`

```text
repo    bitcoin/bitcoin
path    test/functional/test_framework/wallet_util.py
commit  42330922dd8d5f96dbc0cc6a8e4092500029f89d  2026-05-27
behind  0 revisions; that commit is the tip of the path
```

Verdict: **covered in part**. `tx.input_weight` is
`calculate_input_weight`
([ISS 1067](https://github.com/btclib-org/btclib/issues/1067)), and
`b58.wif_from_prv_key` is `bytes_to_wif`. `get_generate_key`,
`test_address` and `WalletUnlock` need a node.

## The files that are not Python

The directory also carries `bip340_test_vectors.csv`,
`crypto/ellswift_decode_test_vectors.csv` and
`crypto/xswiftec_inv_test_vectors.csv`. Those are Core's copies of
bitcoin/bips files, and btclib vendors the bips originals instead, each
pinned against bitcoin/bips in its own `tests/_data/README.md`. No entry
above pins Core's copy, on purpose: pinning a copy of an upstream makes a
record drift for a reason that is not its subject's.

## The per-test ledger

Rule 5 of [ISS btclib-org/btclib#2220](https://github.com/btclib-org/btclib/issues/2220):
"The ledger gains a second table in tf2: one row per Core test, pinned,
with its verdict on each node." This is that table -- Core's own
functional tests, `test/functional/*.py`, each pinned the way the ledger
above pins a framework file, and each given one verdict per node this
repository's adapter reaches.

A verdict is **pass**; **fail**, naming the issue a disagreement is
filed as on `btclib-node`'s own tracker (rule 3); or **skip**, naming
the capability ([`capability.py`](./src/btclib_functional_tests/capability.py))
the node, or the wallet factory, does not declare; or **not ported**,
where every capability the row asks for is declared and the test run
against it is still a stub ending in `pytest.fail`
([ISS 82](https://github.com/btclib-org/functional-tests/issues/82));
or **flaky**, naming the issue its race is filed as, where one build
passes the test on some runs and fails it on others, so that either
outcome agrees with it. A **fail** or a **pass** there is false on part
of the runs: `btclib_node_verdict.py`'s compare then turns red on pull
requests that change neither the test nor the node, and teaches
rerunning it until it is green. Once that issue is closed, the pull
request that reads a run passing the test on a build carrying its fix
moves that build's verdict to **pass**; the compare lists each flaky row
a run passed, apart from what moved. bitcoind is the oracle (rule 3): a
**fail** on bitcoind is this repository's own defect rather than a
finding for another tracker.

A `btclib-node` **fail** names an issue of this repository instead where
the test is the defect, not the node: a test that misreads a fact
btclib-node gets right fails there until this repository fixes it.

btcd has no column. Its verdicts are those of the pytest run in
`node-integration.yml`'s `btcd` job, over each
`tests/integration/*_btcd_test.py` against the btcd release the job
installs, and no script compares them with this table.
`README.md`'s opening says when a test btcd fails is a finding.

A row whose subject is bitcoind's own option carries **bitcoind only**
in its `btclib-node` column instead: not a verdict on that node, since
no test of this shape ever runs against one. `capability.py`'s own
module docstring is the one place that states which options this
covers, and does not decide it row by row here.

A `btclib-node` cell gives the verdict of each build the latest
`node-integration.yml` run on `main` measures: the PyPI release and
btclib-node's own `main`. Where they agree, the cell is that verdict
alone. Where they differ, the cell reads `<verdict> on the build;
<verdict> on a build past [ISS ...]`: the release's first, then `main`'s,
the issue being the one whose fix `main` carries. A cell keeps no verdict
of an older build; git holds the history.
`.github/scripts/btclib_node_verdict.py` compares each cell with both runs.

A `fail` names the issue it is filed as. A test whose call ended and
whose teardown then timed out is not a `fail` on that account: the script
lists it under a heading of its own
([ISS btclib-node#1274](https://github.com/btclib-org/btclib-node/issues/1274)).

A `bitcoind` cell can be build-dependent the same way, for a fact of
the pinned release's own binary that another build of bitcoind answers
differently -- `capability.py`'s own module docstring is the one rule
this covers, stated once rather than repeated per row: the class says
what every build of a node can do, and a fact that varies release to
release of the *same* binary is read from the one under test instead of
fixed for the whole verdict. `feature_torcontrol.py`'s and
`p2p_bip434_feature.py`'s own rows below are this table's own instances
of it.

A `btclib-node` cell whose outcome depends on timing is read from the
*Compare the report with TF2.md* steps of the last few `main` runs of
`node-integration.yml`, rather than from the latest run alone. A race
can go either way from one run to the next, as
[ISS 274](https://github.com/btclib-org/functional-tests/issues/274)
measured.

A pin or date cell reading **same** repeats the pin and date of the
nearest row above it that names the same Core test file, the row width
leaving no room to write them again.

The `census` job of `vendored-vectors.yml` reads this table's pins
weekly. `.github/scripts/tf2_ledger_census.py` compares each Core test
file's pin with that file's last commit on Core's default branch, and
opens or updates an issue of its own listing each pin behind. A pin cell
the script cannot read fails the job.

A pin cell reading **held** after its sha is behind upstream on purpose.
A row takes it once the change upstream has been read and the port
stays at its pin; the file's line below the table says why. The script
leaves a held pin out of its issue unless the pin is at its path's tip,
its marker then stale, or no commit upstream touches its path.

To re-check one pin by hand:

```shell
entry_path=test/functional/<test>.py
gh api --method GET repos/bitcoin/bitcoin/commits \
    -f "path=${entry_path:?}" -f per_page=1 \
    --jq '.[0].sha + "  " + .[0].commit.committer.date[:10]'
```

| Core test | pin | read at | bitcoind | btclib-node |
| --- | --- | --- | --- | --- |
| `feature_blocksdir.py` | `0d1301b47a35` | 2026-03-24 | pass | skip (blk) |
| `feature_filelock.py` | `fa5f29774872` | 2025-12-16 | pass | pass |
| `rpc_whitelist.py` | [`fad441867c9b`](https://github.com/bitcoin/bitcoin/commit/fad441867c9b) | 2026-10-12 | pass | pass |
| `rpc_whitelist.py` (rejected method, log) | same | same | pass, the newline asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (debug_log) |
| `rpc_users.py` | [`fad441867c9b`](https://github.com/bitcoin/bitcoin/commit/fad441867c9b) | 2026-10-12 | pass | pass |
| `rpc_users.py` (`-norpcauth`) | same | same | pass | pass |
| `rpc_users.py` (`-rpcuser`/`-rpcpassword`) | same | same | pass | pass |
| `rpc_users.py` (`-norpccookiefile`) | same | same | pass | pass |
| `p2p_block_sync.py` | `fa5f29774872` | 2025-12-16 | pass | pass |
| `p2p_compactblocks_hb.py` | `fa5f29774872` | 2025-12-16 | pass | pass |
| `p2p_getdata.py` | `aaf941202667` | 2026-07-31 | pass | pass |
| `p2p_invalid_locator.py` | `fa5f29774872` | 2025-12-16 | pass | pass |
| `p2p_invalid_messages.py` (wire) | `3fd68a95e68b` | 2026-04-07 | pass | pass |
| `p2p_invalid_messages.py` (log) | `3fd68a95e68b` | 2026-04-07 | pass | skip |
| `p2p_invalid_messages.py` (inv, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (inv, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (getdata, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (getdata, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (headers, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (headers, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (invalid pow, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (invalid pow, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (size, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (size, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (dup version, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (dup version, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (checksum, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (checksum, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (msgtype, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (msgtype, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (addrv2 empty, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (addrv2 empty, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (addrv2 no addr, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (addrv2 no addr, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (addrv2 long, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (addrv2 long, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (addrv2 net id, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (addrv2 net id, log) | same | same | pass | skip |
| `p2p_invalid_messages.py` (noncontinuous, wire) | same | same | pass | pass |
| `p2p_invalid_messages.py` (noncontinuous, log) | same | same | pass | skip |
| `p2p_leak.py` (wire) | `36775471f81a` | 2026-09-09 | pass | pass |
| `p2p_leak.py` (log) | same | same | pass | skip |
| `p2p_leak.py` (version boundary) | same | same | pass | pass |
| `p2p_handshake.py` (wire) | `3fd68a95e68b` | 2026-04-07 | pass | pass |
| `p2p_handshake.py` (log) | same | same | pass | skip |
| `p2p_handshake.py` (services, wire) | same | same | pass | pass |
| `p2p_handshake.py` (services, log) | same | same | pass | skip |
| `p2p_handshake.py` (limited, wire) | same | same | pass | pass |
| `p2p_handshake.py` (limited, log) | same | same | pass | skip |
| `p2p_handshake.py` (feeler, wire) | same | same | pass | pass |
| `p2p_handshake.py` (feeler, log) | same | same | pass | skip |
| `p2p_handshake.py` (self, wire) | same | same | pass | pass |
| `p2p_handshake.py` (self, log) | same | same | pass | skip |
| `p2p_addr_relay.py` (wire) | `b7211ba80cde` | 2026-09-22 | pass | pass |
| `p2p_addr_relay.py` (log) | same | same | pass | skip |
| `p2p_addrv2_relay.py` (wire) | `fa5f29774872` | 2025-12-16 | pass | pass |
| `p2p_addrv2_relay.py` (log) | same | same | pass | skip |
| `p2p_net_deadlock.py` | `a0473442d1c2` | 2024-07-16 | pass | skip (raw_msg) |
| `feature_uacomment.py` | `fa5f29774872` | 2025-12-16 | pass | skip |
| `rpc_uptime.py` | `406c2348ddbf` | 2026-06-13 | pass, the mock clock followed or not per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | fail ([ISS btclib-node#2119](https://github.com/btclib-org/btclib-node/issues/2119)) |
| `feature_torcontrol.py` | `4556ef626754` | 2026-09-15 | pass, the `PoWDefensesEnabled` flag asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | bitcoind only |
| `p2p_bip434_feature.py` | `da74ff9ca49e` | 2026-06-04 | pass, `FEATURE`'s own disconnects asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | bitcoind only |
| `feature_framework_miniwallet.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | pass |
| `feature_framework_miniwallet.py` (`confirmed_only`) | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | pass |
| `feature_framework_miniwallet.py` (`fee_rate`) | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (datacarrier) |
| `feature_framework_miniwallet.py` (TRUC) | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (datacarrier) |
| `mempool_resurrect.py` | `fa5f29774872` | 2025-12-16 | pass | pass |
| `mempool_spend_coinbase.py` | `6eca11175be6` | 2026-07-16 | pass | pass |
| `feature_dersig.py` | `fa6f7e36a8c2` | 2026-10-23 | pass | fail ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)) |
| `feature_dersig.py` (wire) | same | same | pass | pass |
| `feature_dersig.py` (log) | same | same | pass | skip |
| `feature_dersig.py` (signature) | same | same | pass | fail ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)) |
| `feature_cltv.py` | `fa6f7e36a8c2` | 2026-10-23 | pass | fail ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)) |
| `feature_cltv.py` (wire) | same | same | pass | pass |
| `feature_cltv.py` (log) | same | same | pass | skip |
| `feature_cltv.py` (failures, activation) | same | same | pass | fail ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)) |
| `feature_cltv.py` (failures, mempool) | same | same | pass | skip |
| `feature_cltv.py` (failures, block) | same | same | pass | pass |
| `feature_csv_activation.py` | `fa6f7e36a8c2` | 2026-10-23 | pass | fail ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)) |
| `feature_csv_activation.py` (lock times) | same | same | pass | fail ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)) |
| `feature_nulldummy.py` | `fa5f29774872` | 2025-12-16 | pass | pass |
| `feature_dirsymlinks.py` | `fa5f29774872` | 2025-12-16 | pass | pass |
| `feature_posix_fs_permissions.py` | [`3fd68a95e68b`](https://github.com/bitcoin/bitcoin/commit/3fd68a95e68b) | 2026-04-07 | pass | pass |
| `rpc_createmultisig.py` | `771200ca4362` | 2026-06-30 | pass | bitcoind only |
| `rpc_createmultisig.py` (spend) | same | same | pass, `combinerawtransaction`'s mergeability refusals asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `rpc_setban.py` (ban) | `fa21edddb272` | 2026-03-27 | pass | pass |
| `rpc_setban.py` (restart) | same | same | pass | skip (debug_log) |
| `rpc_setban.py` (noban) | same | same | pass | pass |
| `rpc_setban.py` (non-IP) | same | same | pass | pass |
| `rpc_setban.py` (bantime) | same | same | pass | pass |
| `p2p_disconnect_ban.py` (disconnectnode) | [`dcd90fbe54cf`](https://github.com/bitcoin/bitcoin/commit/dcd90fbe54cf) | 2026-04-07 | pass | pass |
| `mempool_datacarrier.py` | `fa5f29774872` | 2025-12-16 | pass, the policy asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip |
| `mempool_dust.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (dust_relay_fee) |
| `mempool_sigoplimit.py` | `5d25a0c28d19` | 2026-07-07 | pass | skip |
| `mempool_package_limits.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass; skip (limit_cluster_count) on a build before the cluster mempool | skip (limit_cluster_count) |
| `mempool_updatefromblock.py` | `fa6b05c96ffb` | 2026-03-12 | pass, the limits asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_leak_tx.py` (in block) | `fa5f29774872` | 2025-12-16 | pass, the `getpeerinfo` fields asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | pass |
| `p2p_leak_tx.py` (replaced) | same | same | pass | pass |
| `p2p_leak_tx.py` (unannounced) | same | same | pass | pass |
| `feature_utxo_set_hash.py` | `58eeab790d98` | 2026-05-13 | pass | pass |
| `rpc_getdescriptoractivity.py` | `3fd68a95e68b` | 2026-04-07 | pass | skip |
| `rpc_getdescriptoractivity.py` (mempool) | same | same | pass | skip |
| `rpc_getblockstats.py` | `b7cbd804284b` | 2026-05-25 | pass | skip (stats) |
| `feature_fastprune.py` | `fa5f29774872` | 2025-12-16 | pass | skip |
| `rpc_scanblocks.py` | `aeca0610865e` | 2026-07-01 | pass | skip |
| `rpc_scanblocks.py` (no index) | same | same | pass | skip |
| `p2p_eviction.py` | [`fa9a9657506d`](https://github.com/bitcoin/bitcoin/commit/fa9a9657506d) | 2026-10-15 | pass, `-maxconnections` read per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `feature_presegwit_node_upgrade.py` | [`fad7bd9ba3ee`](https://github.com/bitcoin/bitcoin/commit/fad7bd9ba3ee) | 2026-01-14 | pass, the refusal's leading `": "` asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | fail ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)) |
| `rpc_validateaddress.py` | [`a4bc96966faa`](https://github.com/bitcoin/bitcoin/commit/a4bc96966faa) | 2026-09-29 | pass | skip (validate_address) |
| `p2p_addrfetch.py` | [`3fd68a95e68b`](https://github.com/bitcoin/bitcoin/commit/3fd68a95e68b) | 2026-04-07 | pass | pass |
| `rpc_echo_payload.py` | [`fa7bc26d1276`](https://github.com/bitcoin/bitcoin/commit/fa7bc26d1276) | 2026-08-06 | pass, a connection per call on a libevent build ([ISS 415](https://github.com/btclib-org/functional-tests/issues/415)) | skip (rpc_work_queue) |
| `p2p_compactblocks_blocksonly.py` | [`bf9884f4e55d`](https://github.com/bitcoin/bitcoin/commit/bf9884f4e55d) | 2026-06-18 | pass, the ignored `cmpctblock` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (blocks_only) |
| `rpc_getblockfilter.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (block_filter_index) |
| `rpc_getblockfrompeer.py` | [`779f4446803d`](https://github.com/bitcoin/bitcoin/commit/779f4446803d) | 2026-05-25 | pass | skip (block_from_peer) |
| `p2p_node_network_limited.py` | [`fadcd0b64e39`](https://github.com/bitcoin/bitcoin/commit/fadcd0b64e39) | 2026-10-15 | pass | pass |
| `rpc_getdescriptorinfo.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass, the whitespace refusal's wording asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip (descriptor_info) |
| `p2p_timeouts.py` (wire) | [`fa4cb96bdec2`](https://github.com/bitcoin/bitcoin/commit/fa4cb96bdec2) | 2026-02-17 | pass | skip (peer_timeout) |
| `p2p_timeouts.py` (log) | same | same | pass | skip (peer_timeout) |
| `p2p_timeouts.py` (refusal) | same | same | pass | skip (peer_timeout) |
| `p2p_ping.py` (wire) | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (peer_timeout) |
| `p2p_ping.py` (log) | same | same | pass | skip (peer_timeout) |
| `mempool_expiry.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (mempool_expiry) |
| `p2p_add_connections.py` | [`4c79f3a34d00`](https://github.com/bitcoin/bitcoin/commit/4c79f3a34d00) | 2026-09-14 | pass | pass |
| `p2p_add_connections.py` (`manual`) | same | same | pass, `manual` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_v2_encrypted.py` (inbound) | [`fab352053d6e`](https://github.com/bitcoin/bitcoin/commit/fab352053d6e) | 2026-04-16 | pass | pass |
| `p2p_v2_encrypted.py` (outbound) | same | same | pass | pass |
| `feature_includeconf.py` (order) | [`fa71c15f8610`](https://github.com/bitcoin/bitcoin/commit/fa71c15f8610) | 2025-11-26 | pass | skip (ua_comment) |
| `feature_includeconf.py` (double negative) | same | same | pass | pass |
| `feature_includeconf.py` (`-includeconf`) | same | same | pass | pass |
| `feature_includeconf.py` (nested) | same | same | pass | pass |
| `feature_includeconf.py` (missing) | same | same | pass | pass |
| `feature_reindex_init.py` | [`0d1301b47a35`](https://github.com/bitcoin/bitcoin/commit/0d1301b47a35) | 2026-03-24 | pass, the refusal's leading `": "` asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)); skip (reindex_after_failure) on a build whose `-test` lacks the reindex value | skip (reindex_after_failure) |
| `rpc_generate.py` | [`6eca11175be6`](https://github.com/bitcoin/bitcoin/commit/6eca11175be6) | 2026-07-16 | pass | fail ([ISS btclib-node#2126](https://github.com/btclib-org/btclib-node/issues/2126)) |
| `rpc_signrawtransactionwithkey.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | pass |
| `rpc_scantxoutset.py` | [`b388674acf06`](https://github.com/bitcoin/bitcoin/commit/b388674acf06) | 2026-08-06 | pass, `start`'s refusal of a null scan-object list asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `feature_proxy.py` | [`fd65406b39a8`](https://github.com/bitcoin/bitcoin/commit/fd65406b39a8) | 2026-10-01 | pass; skip (proxy_per_network) on a build whose `-help` shows no `-proxy` suffix, the malformed suffix starts' refusal asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)), and the `tor` refusal, the network's case and the connection log line asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (proxy) |
| `feature_proxy.py` (`-cjdnsreachable`) | same | same | pass | skip (cjdns) |
| `feature_proxy.py` (`-i2psam`) | same | same | pass | skip (i2p_sam) |
| `feature_proxy.py` (`-onlynet`) | same | same | pass, the `tor` refusal asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (onlynet) |
| `wallet_signmessagewithaddress.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `wallet_blank.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `wallet_coinbase_category.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `p2p_initial_headers_sync.py` | [`fad763752942`](https://github.com/bitcoin/bitcoin/commit/fad763752942) | 2026-10-16 | pass | pass |
| `p2p_initial_headers_sync.py` (stall, wire) | same | same | pass | skip |
| `p2p_initial_headers_sync.py` (stall, log) | same | same | pass | skip |
| `p2p_initial_headers_sync.py` (noban, wire) | same | same | pass | skip |
| `p2p_initial_headers_sync.py` (noban, log) | same | same | pass | skip |
| `p2p_sendtxrcncl.py` | [`2fabbc0bb38d`](https://github.com/bitcoin/bitcoin/commit/2fabbc0bb38d) | 2026-09-09 | pass | skip (tx_reconciliation) |
| `p2p_sendtxrcncl.py` (bloom) | same | same | pass | skip (tx_reconciliation) |
| `p2p_sendtxrcncl.py` (outbound) | same | same | pass | skip |
| `p2p_sendtxrcncl.py` (outbound, log) | same | same | pass | skip |
| `p2p_sendtxrcncl.py` (blocksonly) | same | same | pass | skip |
| `p2p_sendtxrcncl.py` (off) | same | same | pass | pass |
| `p2p_sendtxrcncl.py` (off, log) | same | same | pass | skip (debug_log) |
| `p2p_sendtxrcncl.py` (violations, wire) | same | same | pass | skip |
| `p2p_sendtxrcncl.py` (violations, log) | same | same | pass | skip |
| `p2p_sendtxrcncl.py` (kept, wire) | same | same | pass | skip |
| `p2p_sendtxrcncl.py` (kept, log) | same | same | pass | skip |
| `feature_reindex.py` (reindex) | [`9e6546c517cd`](https://github.com/bitcoin/bitcoin/commit/9e6546c517cd) | 2026-06-21 | pass | skip (reindex) |
| `feature_reindex.py` (out of order) | same | same | pass | skip (reindex) |
| `feature_reindex.py` (interrupted) | same | same | pass | skip (reindex) |
| `feature_reindex_readonly.py` | [`6eca11175be6`](https://github.com/bitcoin/bitcoin/commit/6eca11175be6) | 2026-07-16 | pass | skip (reindex) |
| `p2p_feefilter.py` | [`fa6f7e36a8c2`](https://github.com/bitcoin/bitcoin/commit/fa6f7e36a8c2) | 2026-10-23 | pass | pass |
| `p2p_feefilter.py` (forcerelay) | same | same | pass | pass |
| `p2p_feefilter.py` (filter) | same | same | pass | pass |
| `p2p_feefilter.py` (block-relay-only) | same | same | pass | pass |
| `p2p_feefilter.py` (blocksonly) | same | same | pass | skip |
| `p2p_mutated_blocks.py` (wire) | [`9c5dd2926aa9`](https://github.com/bitcoin/bitcoin/commit/9c5dd2926aa9) | 2026-06-18 | pass | pass |
| `p2p_mutated_blocks.py` (log) | same | same | pass | skip |
| `p2p_mutated_blocks.py` (missing parent, wire) | same | same | pass | pass |
| `p2p_mutated_blocks.py` (missing parent, log) | same | same | pass | skip |
| `feature_anchors.py` | [`2f7622e3f6f7`](https://github.com/bitcoin/bitcoin/commit/2f7622e3f6f7) | 2026-10-17 | pass, the network-off step asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (debug_log) |
| `feature_anchors.py` (onion) | same | same | pass, the network-toggle steps asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (proxy) |
| `p2p_addr_selfannouncement.py` (inbound, wire) | [`dab7f2c984bd`](https://github.com/bitcoin/bitcoin/commit/dab7f2c984bd) | 2026-07-07 | pass, the first address message asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip |
| `p2p_addr_selfannouncement.py` (inbound, log) | same | same | pass, the first address message asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip |
| `p2p_addr_selfannouncement.py` (outbound, wire) | same | same | pass | skip |
| `p2p_addr_selfannouncement.py` (outbound, log) | same | same | pass | skip |
| `p2p_addr_selfannouncement.py` (`-onlynet`) | same | same | pass, the onion `-externalip` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip |
| `p2p_message_capture.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (capture_messages) |
| `feature_blocksxor.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (blocks_xor) |
| `feature_remove_pruned_files_on_startup.py` | [`fa9aced8006b`](https://github.com/bitcoin/bitcoin/commit/fa9aced8006b) | 2025-01-22 | pass | skip (fastprune) |
| `wallet_createwalletdescriptor.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `wallet_sendmany.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `wallet_timelock.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `wallet_simulaterawtx.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `wallet_rescan_unconfirmed.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (node_wallet) |
| `wallet_miniscript_decaying_multisig_descriptor_psbt.py` | [`88f802983571`](https://github.com/bitcoin/bitcoin/commit/88f802983571) | 2026-01-31 | pass | skip (node_wallet) |
| `wallet_anchor.py` | [`609d265ebc51`](https://github.com/bitcoin/bitcoin/commit/609d265ebc51) | 2025-09-03 | pass | skip (node_wallet) |
| `wallet_disable.py` | [`3fd68a95e68b`](https://github.com/bitcoin/bitcoin/commit/3fd68a95e68b) | 2026-04-07 | pass | bitcoind only |
| `wallet_balance.py` | [`fa6f7e36a8c2`](https://github.com/bitcoin/bitcoin/commit/fa6f7e36a8c2) | 2026-10-23 | pass | skip (node_wallet) |
| `mempool_accept_wtxid.py` | [`fa704c5d3caf`](https://github.com/bitcoin/bitcoin/commit/fa704c5d3caf) | 2026-10-23 | pass | pass |
| `rpc_orphans.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | pass |
| `mining_template_verification.py` | [`6eca11175be6`](https://github.com/bitcoin/bitcoin/commit/6eca11175be6) | 2026-07-16 | pass | pass |
| `p2p_i2p_ports.py` | [`fa20275db32c`](https://github.com/bitcoin/bitcoin/commit/fa20275db32c) | 2025-10-21 | pass | skip (i2p_sam) |
| `p2p_i2p_sessions.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass, the session lines' wording asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip (i2p_sam) |
| `p2p_dns_seeds.py` | [`fa4cb96bdec2`](https://github.com/bitcoin/bitcoin/commit/fa4cb96bdec2) | 2026-02-17 | pass | skip (dns_seed) |
| `p2p_seednode.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (address_fetch) |
| `p2p_ibd_stalling.py` (wire) | [`24628d3ae7dc`](https://github.com/bitcoin/bitcoin/commit/24628d3ae7dc) | 2026-09-14 | pass | pass |
| `p2p_ibd_stalling.py` (log) | same | same | pass, `Stall started`'s absence once the block withheld first is sent asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip (debug_log) |
| `p2p_ibd_stalling.py` (`manual`, wire) | same | same | pass, `manual` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_ibd_stalling.py` (`manual`, log) | same | same | pass, `manual` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (debug_log) |
| `p2p_private_broadcast.py` | [`ac6b6c1f06e9`](https://github.com/bitcoin/bitcoin/commit/ac6b6c1f06e9) | 2026-08-18 | pass, the refusals without `-privatebroadcast` and `attempts_remaining` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (private_broadcast) |
| `p2p_tx_download.py` (expiry) | [`1278a5970d5a`](https://github.com/bitcoin/bitcoin/commit/1278a5970d5a) | 2026-08-06 | pass | pass |
| `p2p_tx_download.py` (disconnect) | same | same | pass | pass |
| `p2p_tx_download.py` (notfound) | same | same | pass | pass |
| `p2p_tx_download.py` (tiebreak) | same | same | pass | pass |
| `p2p_tx_download.py` (inbound) | same | same | pass | pass |
| `p2p_tx_download.py` (outbound) | same | same | pass | pass |
| `p2p_tx_download.py` (noban) | same | same | pass | pass |
| `p2p_tx_download.py` (txid) | same | same | pass | pass |
| `p2p_tx_download.py` (txid beside wtxid) | same | same | pass | pass |
| `p2p_tx_download.py` (large inv) | same | same | pass | pass |
| `p2p_tx_download.py` (duplicate inv) | same | same | pass, the duplicates asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (debug_log) |
| `p2p_tx_download.py` (spurious notfound) | same | same | pass | pass |
| `p2p_tx_download.py` (in flight) | same | same | pass | pass |
| `p2p_tx_download.py` (inv block) | same | same | pass, `inv_to_send` asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | pass |
| `p2p_tx_download.py` (tx requests) | same | same | pass | pass |
| `p2p_tx_download.py` (rejects) | same | same | pass | skip (maxmempool) |
| `p2p_tx_download.py` (mismatch) | same | same | pass | pass |
| `p2p_blocksonly.py` (`-blocksonly`, wire) | [`22535b460568`](https://github.com/bitcoin/bitcoin/commit/22535b460568) | 2026-10-13 | pass | skip |
| `p2p_blocksonly.py` (`-blocksonly`, log) | same | same | pass | skip |
| `p2p_blocksonly.py` (block-relay-only, wire) | same | same | pass | pass |
| `p2p_blocksonly.py` (block-relay-only, log) | same | same | pass | skip |
| `p2p_orphan_handling.py` (arrival timing) | [`902190b6242a`](https://github.com/bitcoin/bitcoin/commit/902190b6242a) | 2026-11-04 | pass | pass |
| `p2p_orphan_handling.py` (rejected parents) | same | same | pass | pass |
| `p2p_orphan_handling.py` (multiple parents) | same | same | pass | pass |
| `p2p_orphan_handling.py` (overlapping parents) | same | same | pass | pass |
| `p2p_orphan_handling.py` (orphan of orphan) | same | same | pass | pass |
| `p2p_orphan_handling.py` (parent confirmed) | same | same | pass, the reconsideration asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_orphan_handling.py` (inherit rejection) | same | same | pass | pass |
| `p2p_orphan_handling.py` (same txid) | same | same | pass | skip |
| `p2p_orphan_handling.py` (same txid parent) | same | same | pass | pass |
| `p2p_orphan_handling.py` (txid inv) | same | same | pass | pass |
| `p2p_orphan_handling.py` (prefer outbound) | same | same | pass | pass |
| `p2p_orphan_handling.py` (announcers) | same | same | pass | pass |
| `p2p_orphan_handling.py` (parents change) | same | same | pass | pass |
| `p2p_orphan_handling.py` (maximal package) | same | same | pass, a connection per call on a libevent build ([ISS 461](https://github.com/btclib-org/functional-tests/issues/461)) | skip |
| `p2p_compactblocks.py` (sendcmpct) | [`b9788a5a10bd`](https://github.com/bitcoin/bitcoin/commit/b9788a5a10bd) | 2026-09-30 | pass | pass |
| `p2p_compactblocks.py` (construction) | same | same | pass | pass |
| `p2p_compactblocks.py` (requests) | same | same | pass | pass |
| `p2p_compactblocks.py` (getblocktxn requests) | same | same | pass | pass |
| `p2p_compactblocks.py` (getblocktxn handler) | same | same | pass | pass |
| `p2p_compactblocks.py` (not at tip) | same | same | pass | pass |
| `p2p_compactblocks.py` (low work, wire) | same | same | pass | pass |
| `p2p_compactblocks.py` (low work, log) | same | same | pass | skip |
| `p2p_compactblocks.py` (incorrect blocktxn) | same | same | pass | pass |
| `p2p_compactblocks.py` (end to end) | same | same | pass | pass |
| `p2p_compactblocks.py` (invalid tx) | same | same | pass | pass |
| `p2p_compactblocks.py` (empty getblocktxn, wire) | same | same | pass, the disconnect asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_compactblocks.py` (empty getblocktxn, log) | same | same | pass, the disconnect asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip |
| `p2p_compactblocks.py` (multiple blocktxn, wire) | same | same | pass | pass |
| `p2p_compactblocks.py` (multiple blocktxn, log) | same | same | pass | skip |
| `p2p_compactblocks.py` (invalid sendcmpct, wire) | same | same | pass, the disconnect asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_compactblocks.py` (invalid sendcmpct, log) | same | same | pass, the disconnect asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip |
| `p2p_compactblocks.py` (invalid cmpctblock) | same | same | pass | pass |
| `p2p_compactblocks.py` (invalid cmpctblock, optimistic) | same | same | pass, the disconnect asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | fail ([ISS btclib-node#2170](https://github.com/btclib-org/btclib-node/issues/2170)) |
| `p2p_compactblocks.py` (sendcmpct, outbound) | same | same | pass | pass |
| `p2p_compactblocks.py` (stalling peer) | same | same | pass | pass |
| `p2p_compactblocks.py` (parallel reconstruction) | same | same | pass | pass |
| `p2p_compactblocks.py` (high-bandwidth states) | same | same | pass | pass |
| `p2p_compactblocks.py` (ignored) | same | same | pass, the ignored `cmpctblock` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `feature_startupnotify.py` | [`fa71c15f8610`](https://github.com/bitcoin/bitcoin/commit/fa71c15f8610) | 2025-11-26 | pass | skip (startup_notify) |
| `rpc_dumptxoutset.py` | [`58eeab790d98`](https://github.com/bitcoin/bitcoin/commit/58eeab790d98) | 2026-05-13 | pass, the dump at a forked height asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `feature_loadblock.py` | [`fa4fc8c1d7b5`](https://github.com/bitcoin/bitcoin/commit/fa4fc8c1d7b5) | 2026-05-22 | pass | skip (load_block) |
| `feature_port.py` | [`997757dd2b4d`](https://github.com/bitcoin/bitcoin/commit/997757dd2b4d) | 2024-11-15 | pass | skip (debug_log) |
| `p2p_opportunistic_1p1c.py` (parent first) | [`86ff13a0af06`](https://github.com/bitcoin/bitcoin/commit/86ff13a0af06) | 2026-10-01 | pass | skip |
| `p2p_opportunistic_1p1c.py` (parent first, P2PK) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (child first) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (low, high child) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (low, high, P2PK) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (orphan invalid) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (parent invalid) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (multiple parents) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (parent in mempool) | same | same | pass, the package asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip |
| `p2p_opportunistic_1p1c.py` (1p1c on 1p1c) | same | same | pass | skip |
| `p2p_opportunistic_1p1c.py` (DoS, large orphans) | same | same | pass, the eviction asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)), a connection per call on a libevent build ([ISS 431](https://github.com/btclib-org/functional-tests/issues/431)) | skip |
| `p2p_opportunistic_1p1c.py` (DoS, many orphans) | same | same | pass, the orphanage asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip |
| `p2p_block_times.py` | [`5d5397d84108`](https://github.com/bitcoin/bitcoin/commit/5d5397d84108) | 2026-07-25 | pass, `last_block_announcement` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | fail ([ISS btclib-node#2171](https://github.com/btclib-org/btclib-node/issues/2171)) |
| `p2p_outbound_eviction.py` (unprotected) | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip |
| `p2p_outbound_eviction.py` (protected) | same | same | pass | skip |
| `p2p_outbound_eviction.py` (mixed) | same | same | pass | skip |
| `p2p_outbound_eviction.py` (block-relay-only) | same | same | pass | skip |
| `p2p_tx_privacy.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | pass |
| `p2p_invalid_block.py` (wire) | [`fa6f7e36a8c2`](https://github.com/bitcoin/bitcoin/commit/fa6f7e36a8c2) | 2026-10-23 | pass | pass |
| `p2p_invalid_block.py` (log) | same | same | pass | skip (debug_log) |
| `feature_maxtipage.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (max_tip_age) |
| `p2p_blockfilters.py` | [`3fd68a95e68b`](https://github.com/bitcoin/bitcoin/commit/3fd68a95e68b) | 2026-04-07 | pass | skip (peer_block_filters) |
| `p2p_getaddr_caching.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (peer_timeout) |
| `mempool_reorg.py` (coinbase) | [`fa3aeb51cfb1`](https://github.com/bitcoin/bitcoin/commit/fa3aeb51cfb1) | 2026-10-26 | pass | pass |
| `mempool_reorg.py` (relay) | same | same | pass | pass |
| `interface_rpc.py` (getrpcinfo) | [`fa2bd96cc0d4`](https://github.com/bitcoin/bitcoin/commit/fa2bd96cc0d4) | 2026-08-06 | pass | pass |
| `interface_rpc.py` (batch) | same | same | pass | pass |
| `interface_rpc.py` (status codes) | same | same | pass | pass |
| `interface_rpc.py` (notifications) | same | same | pass | pass |
| `interface_rpc.py` (work queue) | same | same | pass | skip (rpc_work_queue) |
| `p2p_ibd_txrelay.py` (wire) | [`7777598f87ea`](https://github.com/bitcoin/bitcoin/commit/7777598f87ea) | 2026-10-22 | pass, the old block's coinbase asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | pass |
| `p2p_ibd_txrelay.py` (log) | same | same | pass, the old block's coinbase asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip (debug_log) |
| `feature_bip68_sequence.py` | [`ab41492c6ba7`](https://github.com/bitcoin/bitcoin/commit/ab41492c6ba7) | 2026-01-09 | pass | fail ([ISS btclib-node#2419](https://github.com/btclib-org/btclib-node/issues/2419)) |
| `mempool_accept.py` | [`eaef8d31118d`](https://github.com/bitcoin/bitcoin/commit/eaef8d31118d) | 2026-07-07 | pass, `vsize_adjusted` and `vsize_bip141` asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)), the null-data and bare-multisig checks per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | pass |
| `mempool_cluster.py` | [`659671ac3db7`](https://github.com/bitcoin/bitcoin/commit/659671ac3db7) | 2026-06-04 | pass; skip (limit_cluster_size) on a build before the cluster mempool | skip (limit_cluster_size) |
| `feature_minchainwork.py` | [`c502b65c007b`](https://github.com/bitcoin/bitcoin/commit/c502b65c007b) | 2026-10-03 | pass | skip (minimum_chain_work) |
| `feature_minchainwork.py` (outbound) | same | same | pass | skip |
| `mempool_packages.py` | [`fa6f7e36a8c2`](https://github.com/bitcoin/bitcoin/commit/fa6f7e36a8c2) | 2026-10-23 | pass, the limits asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (limit_cluster_count) |
| `feature_versionbits_warning.py` | [`5bd990a3ddb1`](https://github.com/bitcoin/bitcoin/commit/5bd990a3ddb1) | 2026-06-03 | pass, the reserved bit asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (alert_notify) |
| `mempool_package_rbf.py` | [`fa704c5d3caf`](https://github.com/bitcoin/bitcoin/commit/fa704c5d3caf) | 2026-10-23 | pass, the replacement limit asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (maxmempool) |
| `p2p_headers_sync_with_minchainwork.py` | [`ff3e2e4ebdce`](https://github.com/bitcoin/bitcoin/commit/ff3e2e4ebdce) | 2026-08-19 | pass | skip (minimum_chain_work) |
| `p2p_unrequested_blocks.py` | [`fab352053d6e`](https://github.com/bitcoin/bitcoin/commit/fab352053d6e) | 2026-04-16 | pass | skip (minimum_chain_work) |
| `p2p_1p1c_network.py` | [`fa6f7e36a8c2`](https://github.com/bitcoin/bitcoin/commit/fa6f7e36a8c2) | 2026-10-23 | pass, the fees asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | pass |
| `mempool_ephemeral_dust.py` | [`fa6f7e36a8c2`](https://github.com/bitcoin/bitcoin/commit/fa6f7e36a8c2) | 2026-10-23 | pass, `test_non_truc` asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | pass |
| `mempool_ephemeral_dust.py` (nonzero dust) | same | same | pass | pass |
| `mempool_ephemeral_dust.py` (reorg) | same | same | pass, the reorg asserted per-build ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | pass |
| `feature_notifications.py` (`-blocknotify`) | [`fa6f7e36a8c2`](https://github.com/bitcoin/bitcoin/commit/fa6f7e36a8c2) | 2026-10-23 | pass | skip (block_notify) |
| `feature_notifications.py` (`-alertnotify`) | same | same | pass, the warning's wording asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (alert_notify) |
| `feature_notifications.py` (`-shutdownnotify`) | same | same | pass | skip |
| `feature_settings.py` | [`0654511e1b93`](https://github.com/bitcoin/bitcoin/commit/0654511e1b93) | 2026-06-17 | pass | skip (settings_file) |
| `rpc_getchaintips.py` | [`fa16bc53d79c`](https://github.com/bitcoin/bitcoin/commit/fa16bc53d79c) | 2026-04-16 | pass | pass |
| `rpc_preciousblock.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | pass |
| `rpc_invalidateblock.py` | [`fab352053d6e`](https://github.com/bitcoin/bitcoin/commit/fab352053d6e) | 2026-04-16 | pass, the ancestors' check run per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `rpc_signmessagewithprivkey.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | pass |
| `feature_chain_tiebreaks.py` | [`20ae9b98eab2`](https://github.com/bitcoin/bitcoin/commit/20ae9b98eab2) | 2026-03-04 | pass, the restart's tip check run per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_sendheaders.py` | [`a7acf277dc39`](https://github.com/bitcoin/bitcoin/commit/a7acf277dc39) | 2026-10-02 | pass | pass |
| `p2p_fingerprint.py` | [`fa16bc53d79c`](https://github.com/bitcoin/bitcoin/commit/fa16bc53d79c) | 2026-04-16 | pass | pass |
| `rpc_estimatefee.py` | [`4056908f0fea`](https://github.com/bitcoin/bitcoin/commit/4056908f0fea) | 2026-09-29 | pass, the `options` checks and the estimator refusal run per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `feature_assumevalid.py` | [`fa16bc53d79c`](https://github.com/bitcoin/bitcoin/commit/fa16bc53d79c) | 2026-04-16 | pass, its log lines read per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (assume_valid) |
| `feature_rbf.py` | [`1a85ca1dff1f`](https://github.com/bitcoin/bitcoin/commit/1a85ca1dff1f) | 2026-04-24 | pass, the replacement rules' wording asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (incremental_relay_fee) |
| `p2p_permissions.py` | [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | 2025-12-16 | pass | skip (debug_log) |
| `p2p_private_broadcast_retry_v1.py` | [`4e8c4bc794c0`](https://github.com/bitcoin/bitcoin/commit/4e8c4bc794c0) | 2026-08-04 | pass | skip (private_broadcast) |
| `p2p_private_broadcast_cap.py` | [`82a02a2a2208`](https://github.com/bitcoin/bitcoin/commit/82a02a2a2208) | 2026-07-07 | pass, the cap asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (private_broadcast) |
| `rpc_net.py` (`addnode`) | [`332c3e8e3e17`](https://github.com/bitcoin/bitcoin/commit/332c3e8e3e17) | 2026-10-01 | pass, the blank address refusal asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (proxy) |
| `rpc_net.py` (service flags) | same | same | pass | skip (proxy) |
| `rpc_net.py` (`getnodeaddresses`) | same | same | pass, the `tor` refusal asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (proxy) |
| `rpc_net.py` (`addpeeraddress`) | same | same | pass, the blank address refusal asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (address_tables) |
| `rpc_net.py` (`getaddrmaninfo`) | same | same | pass | skip (cjdns) |
| `rpc_net.py` (`getrawaddrman`) | same | same | pass | skip (cjdns) |
| `feature_config_args.py` (`-proxy`) | [`2630d8e6c9d6`](https://github.com/bitcoin/bitcoin/commit/2630d8e6c9d6) | 2026-09-22 | pass | skip (proxy) |
| `feature_config_args.py` (`-connect`) | same | same | pass | skip (proxy) |
| `feature_config_args.py` (`-privatebroadcast`) | same | same | pass, the warning's last sentence asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip (proxy) |
| `p2p_nobloomfilter_messages.py` (filtered block, wire) | [`9871fb726cb5`](https://github.com/bitcoin/bitcoin/commit/9871fb726cb5) | 2026-07-31 | pass, the disconnect asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `p2p_nobloomfilter_messages.py` (filtered block, log) | same | same | pass, its log line read per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip |
| `interface_http.py` (excessive request size, wire) | [`228877d7836a`](https://github.com/bitcoin/bitcoin/commit/228877d7836a) | 2026-10-10 | pass, the header-lines refusal asserted per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | pass |
| `interface_http.py` (excessive request size, log) | same | same | pass, its log lines read per-build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)) | skip |
| `feature_block.py` (wire) | [`f82043af507a`](https://github.com/bitcoin/bitcoin/commit/f82043af507a) | 2026-06-30 | pass | skip (peer_timeout) |
| `feature_block.py` (log) | same | same | pass | skip |
| `feature_prune_stale_fork.py` | [`3f44f9aef7cc`](https://github.com/bitcoin/bitcoin/commit/3f44f9aef7cc) | 2026-05-12 | pass, the restart asked only of a build carrying bitcoin/bitcoin#35168 ([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)) | skip (fastprune) |

Each line gives the reason for a file's `btclib-node` cell, why a **held** pin
stays behind, and, where there is one, the issue it is filed as. What differs
from Core's file is in the docstrings of `tests/integration/<file>*_test.py`.
The cell gives the verdict per build; an entry gives the reason only. A row with
no entry here is a `bitcoind only` row, or has its reason in the docstring of
`tests/integration/<file>_btclib_node_test.py`.

- `feature_blocksdir.py`: a smaller claim, Core also mining before its disk
  read. Skips on `blk_files`: btclib-node keeps its own block format, by
  decision
  ([ISS btclib-node#573](https://github.com/btclib-org/btclib-node/issues/573)).
  The refusal is matched on the node's whole stderr
  ([ISS 19](https://github.com/btclib-org/functional-tests/issues/19)).
- `feature_filelock.py`: a smaller claim: the cookie- and PID-file checks and
  the wallet-directory lock are dropped. btclib-node writes Core's lock error
  ([ISS btclib-node#1147](https://github.com/btclib-org/btclib-node/issues/1147)).
- `rpc_whitelist.py`: a smaller claim: named users, not Core's `strange_users`
  roster. Gated on `rpc_auth_config`
  ([ISS btclib-node#1070](https://github.com/btclib-org/btclib-node/issues/1070)).
  The rejected-method log row skips on `debug_log`.
- `rpc_users.py`: a smaller claim: no Windows branch, no bare `-rpcauth`, no
  `share/rpcauth` script. `-norpcauth` is gated on `rpc_auth_negation`
  ([ISS btclib-node#1176](https://github.com/btclib-org/btclib-node/issues/1176));
  `-rpcuser`, `-rpcpassword` and `-norpccookiefile` run through `rpc_auth`
  ([ISS 34](https://github.com/btclib-org/functional-tests/issues/34)). A
  cookie that cannot be written and a malformed `-rpcauth` are refused in Core's
  `Unable to start HTTP server` wording
  ([ISS btclib-node#1210](https://github.com/btclib-org/btclib-node/issues/1210)).
- `p2p_getdata.py`: a smaller claim: the later `getdata` is asked of genesis,
  not of a mined tip, `MINE` not being every node's fact
  ([ISS 2](https://github.com/btclib-org/functional-tests/issues/2),
  [ISS btclib-node#1071](https://github.com/btclib-org/btclib-node/issues/1071)).
- `p2p_block_sync.py`: Core's claim in full. Skips on `mine` where undeclared.
- `p2p_invalid_locator.py`: Core's claim in full. Skips on `mine` where
  undeclared.
- `p2p_compactblocks_hb.py`: Core's claim in full, its peers told apart by
  connection order, not by `-uacomment`.
- `p2p_net_deadlock.py`: Core's claim in full. Skips on `raw_message`, Core's
  `sendmsgtopeer`.
- `p2p_leak.py`: a wire row and a log row per Core check
  ([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)). The
  checks before the handshake completes are not ported: they ask what a node
  sends, not what it logs. The version boundary (bitcoin/bitcoin#36152) is wire
  only.
- `p2p_invalid_messages.py`: a wire row and a log row per Core assertion
  ([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)): magic,
  size, oversized `inv`, `getdata` and headers, invalid proof of work,
  non-continuous headers, duplicate `version`, checksum, message type and the
  `addrv2` checks. The log rows skip on `debug_log`. The non-continuous headers
  rows ask for `mine` too. Core's whitelist permission is dropped: it grants
  `Addr`, not `NoBan`. The `addrv2` rows rely on a peer's messages being
  handled in order
  ([ISS btclib-node#1739](https://github.com/btclib-org/btclib-node/issues/1739)).
- `p2p_handshake.py`: the redundant-`verack` check, a wire row and a log row
  ([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)). The
  other rows have the node dial the test. Their log rows skip on
  `debug_log`.
- `p2p_addr_relay.py`: the first check of Core's `run_test`, a wire row and a
  log row ([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)).
- `p2p_addrv2_relay.py`: the first check of Core's `run_test`, a wire row and a
  log row ([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)).
- `p2p_bip434_feature.py`: narrowed to the disconnects a build
  without BIP434 makes anyway, one before `v32.0rc1` having no `FEATURE`
  message (added with bitcoin/bitcoin@6a129983c9bf); the shape asserted is
  read from the build
  ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
  The length and acceptance checks need `-peertimeout` and belong to
  [ISS 14](https://github.com/btclib-org/functional-tests/issues/14).
- `feature_uacomment.py`: a smaller claim: no `testnode{i}` default comment, no
  length or unsafe-character checks. Skips on `ua_comment`: `cli.py` registers
  no `-uacomment`
  ([ISS 3](https://github.com/btclib-org/functional-tests/issues/3)).
- `rpc_uptime.py`: Core's claim in full. btclib-node serves no `uptime`
  ([ISS btclib-node#2119](https://github.com/btclib-org/btclib-node/issues/2119)).
- `feature_torcontrol.py`:
  [ISS 23](https://github.com/btclib-org/functional-tests/issues/23).
  `-torcontrol` drives a mock Tor control server the test carries. A smaller
  claim: the proof-of-work defenses step is asserted as the build under test
  takes it
  ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
- `p2p_compactblocks_blocksonly.py`: an option-family row
  ([ISS 3](https://github.com/btclib-org/functional-tests/issues/3)).
  Skips on `blocks_only`: `cli.py` registers no `-blocksonly`. What a
  `-blocksonly` node does with a `cmpctblock` is read from the build
  ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).
- `rpc_echo_payload.py`: skips on `rpc_work_queue`: `cli.py` registers neither
  `-rpcworkqueue` nor `-rpcthreads`
  ([ISS 3](https://github.com/btclib-org/functional-tests/issues/3)). A
  smaller claim: the refusal is matched on its HTTP status alone.
- `rpc_getblockfilter.py`: Core's claim in full. Skips on `block_filter_index`:
  no `-blockfilterindex`, no `getblockfilter`.
- `rpc_getblockfrompeer.py`: a smaller claim in its literals alone. Skips on
  `block_from_peer`: no `getblockfrompeer`.
- `p2p_node_network_limited.py`: Core's claim in full
  ([ISS 185](https://github.com/btclib-org/functional-tests/issues/185)).
- `feature_presegwit_node_upgrade.py`: every assertion of Core's file, the
  blocks built in Core's shape because a `MiniWallet` block reindexes to full
  height. Its restarts name new `extra_args`
  ([ISS 51](https://github.com/btclib-org/functional-tests/issues/51)). Fails on
  `getdeploymentinfo`, as `feature_dersig.py`.
- `rpc_validateaddress.py`: Core's claim in full, on the main chain
  ([ISS 153](https://github.com/btclib-org/functional-tests/issues/153),
  [ISS 63](https://github.com/btclib-org/functional-tests/issues/63)). Skips
  on `validate_address`.
- `rpc_getdescriptorinfo.py`: Core's claim without its `-disablewallet` option
  ([ISS 174](https://github.com/btclib-org/functional-tests/issues/174)).
  Skips on `descriptor_info`.
- `feature_framework_miniwallet.py`: a smaller claim: the default address mode
  only, no padding or tagging test
  ([ISS 4](https://github.com/btclib-org/functional-tests/issues/4)).
  `fee_rate` and TRUC
  ([ISS 103](https://github.com/btclib-org/functional-tests/issues/103))
  skip on `datacarrier`. Until it is declared,
  [ISS btclib-node#1397](https://github.com/btclib-org/btclib-node/issues/1397),
  [ISS btclib-node#1398](https://github.com/btclib-org/btclib-node/issues/1398)
  and
  [ISS btclib-node#1399](https://github.com/btclib-org/btclib-node/issues/1399)
  are not reached.
- `mempool_resurrect.py`: `MiniWallet.resync` needs `gettxout`
  ([ISS btclib-node#1388](https://github.com/btclib-org/btclib-node/issues/1388)).
- `mempool_spend_coinbase.py`: a narrower claim: it mines to maturity and never
  calls `invalidateblock`. The immature spend is refused as in Core
  ([ISS btclib-node#1328](https://github.com/btclib-org/btclib-node/issues/1328)).
- `rpc_generate.py`: btclib-node serves no hidden `generate`, so its
  refusal and its help are not Core's
  ([ISS btclib-node#2126](https://github.com/btclib-org/btclib-node/issues/2126)).
- `rpc_scantxoutset.py`: Core's claim in full.
- `mempool_accept_wtxid.py`: a `Peer` stands in for Core's
  `P2PInterface`.
- `rpc_orphans.py`: Core's claim in full.
- `p2p_ibd_stalling.py`: the log rows skip on `debug_log`.
- `p2p_tx_privacy.py`: a `Peer` sends `version` and `wtxidrelay` by hand
  and holds back its `verack`.
- `mempool_package_rbf.py`: the mempool-ancestor body alone restarts with
  `-maxmempool`, and skips on `maxmempool` where the node does not declare
  it.
- `feature_dersig.py`: a smaller claim: kept are `getdeploymentinfo`'s
  transition, the version floor (`bad-version`) and the non-DER signature
  ([ISS 167](https://github.com/btclib-org/functional-tests/issues/167)).
  The rows that read `getdeploymentinfo` fail: btclib-node serves no
  `getdeploymentinfo`
  ([ISS btclib-node#2118](https://github.com/btclib-org/btclib-node/issues/2118)).
- `feature_cltv.py`: as `feature_dersig.py`, with the `OP_CHECKLOCKTIMEVERIFY`
  failure reasons
  ([ISS 167](https://github.com/btclib-org/functional-tests/issues/167)).
  The mempool row also skips on `accept_non_standard`; the block row needs
  only `mine`
  ([ISS btclib-node#1362](https://github.com/btclib-org/btclib-node/issues/1362)).
- `feature_csv_activation.py`: BIP68, BIP112 and BIP113 at Core's configured
  height. No version-floor row: Core's `ContextualCheckBlockHeader` reads no
  `DEPLOYMENT_CSV`. Fails on `getdeploymentinfo`, as `feature_dersig.py`.
- `feature_nulldummy.py`: every step of Core's file
  ([ISS 64](https://github.com/btclib-org/functional-tests/issues/64)),
  the spends signed with btclib
  ([ISS 165](https://github.com/btclib-org/functional-tests/issues/165)).
- `feature_dirsymlinks.py`: Core's claim in full
  ([ISS 7](https://github.com/btclib-org/functional-tests/issues/7)).
  Asks for no capability: the fact is the operating system's symlink
  resolution.
- `feature_posix_fs_permissions.py`: a smaller claim: no `wallets_path` check,
  no node wallet being started
  ([ISS 7](https://github.com/btclib-org/functional-tests/issues/7)).
- `rpc_createmultisig.py`: a smaller claim: `test_sortedmulti_descriptors_bip67`
  is dropped, its vectors being a fixture this repository does not carry
  ([ISS 4](https://github.com/btclib-org/functional-tests/issues/4)). The
  construction row needs `createmultisig`, which btclib-node's dispatch table
  does not name. A script past `OP_16`'s count is built by hand, btclib
  refusing it
  [ISS btclib-org/btclib#2348](https://github.com/btclib-org/btclib/issues/2348).
- `p2p_invalid_block.py`: Core's whole run as a body per row, wire and log.
- `p2p_timeouts.py`: Core's claim in full; a refused start under a non-positive
  `-peertimeout` has a row of its own. Skips on `peer_timeout`: `cli.py`
  registers no `-peertimeout`.
- `p2p_ping.py`: Core's claim in full. Skips on `peer_timeout`, as
  `p2p_timeouts.py`.
- `mempool_expiry.py`: Core's claim in full. Skips on `mempool_expiry`: `cli.py`
  registers no `-mempoolexpiry`.
- `p2p_v2_encrypted.py`: Core's claim, split into an inbound row and an
  outbound row ([ISS 175](https://github.com/btclib-org/functional-tests/issues/175)).
  Both rows pass.
- `feature_includeconf.py`: a row per refusal of Core's file. The order row
  skips on `ua_comment`; the others run
  ([ISS btclib-node#1116](https://github.com/btclib-org/btclib-node/issues/1116),
  [ISS btclib-node#1409](https://github.com/btclib-org/btclib-node/issues/1409),
  [ISS btclib-node#1403](https://github.com/btclib-org/btclib-node/issues/1403),
  [ISS btclib-node#1187](https://github.com/btclib-org/btclib-node/issues/1187),
  [ISS btclib-node#1402](https://github.com/btclib-org/btclib-node/issues/1402)).
- `feature_reindex_init.py`: Core's claim in full. Skips on
  `reindex_after_failure`: `cli.py` registers no `-test`.
- `feature_reindex.py`: a row per step of Core's `run_test`. Skips on `reindex`
  ([ISS btclib-node#1415](https://github.com/btclib-org/btclib-node/issues/1415)).
- `feature_reindex_readonly.py`: Core's claim, with the file's mode alone and
  not the immutable flag. Skips on `reindex`
  ([ISS btclib-node#1415](https://github.com/btclib-org/btclib-node/issues/1415)).
- `p2p_message_capture.py`: Core's claim. Skips on `capture_messages`: `cli.py`
  registers no `-capturemessages`.
- `feature_blocksxor.py`: Core's claim, with the first block file asserted
  obfuscated. Skips on `blocks_xor`: `cli.py` registers no `-blocksxor`.
- `feature_remove_pruned_files_on_startup.py`: Core's claim. Skips on
  `fastprune`: `cli.py` registers no `-fastprune`.
- `feature_startupnotify.py`: Core's claim. Skips on `startup_notify`
  ([ISS btclib-node#1449](https://github.com/btclib-org/btclib-node/issues/1449)).
- `rpc_dumptxoutset.py`: every check of Core's file but the hashes it asserts as
  constants, asserted against the same node.
- `feature_loadblock.py`: Core's claim, the test writing the block file itself
  in the format `linearize-data.py` writes. Skips on `load_block`, left out by
  decision
  ([ISS btclib-node#573](https://github.com/btclib-org/btclib-node/issues/573)).
- `feature_port.py`: Core's claim; bitcoind's test runs on
  `UnboundBitcoindAdapter`, which leaves out its `-bind`. Skips on
  `debug_log`. Onion binds:
  ([ISS btclib-node#1644](https://github.com/btclib-org/btclib-node/issues/1644),
  [ISS btclib-node#1666](https://github.com/btclib-org/btclib-node/issues/1666)).
- `feature_maxtipage.py`: Core's claim, its blocks built client-side. Skips on
  `max_tip_age`
  ([ISS btclib-node#1474](https://github.com/btclib-org/btclib-node/issues/1474)).
- `p2p_blockfilters.py`: Core's claim in full. Skips on `peer_block_filters`
  ([ISS btclib-node#1395](https://github.com/btclib-org/btclib-node/issues/1395)).
- `p2p_getaddr_caching.py`: Core's claim, bitcoind's test running on
  `UnboundBitcoindAdapter`. Skips on `peer_timeout`.
- `mempool_reorg.py`: Core's claim in full.
- `interface_rpc.py`: the work queue step is read off the HTTP reply, not
  through `bitcoin-cli`
  ([ISS 49](https://github.com/btclib-org/functional-tests/issues/49)).
  The `getrpcinfo` row reads btclib-node's own log, `history.log`, where
  bitcoind's is `debug.log`. The work queue row skips on `rpc_work_queue`.
  The batch and status rows rest on requests being parsed as Core does
  ([ISS btclib-node#1109](https://github.com/btclib-org/btclib-node/issues/1109)).
- `p2p_ibd_txrelay.py`: Core's claim. The top `feefilter` bucket follows
  `-minrelaytxfee`, not Core's default
  ([ISS btclib-node#1374](https://github.com/btclib-org/btclib-node/issues/1374)).
- `feature_bip68_sequence.py`: Core's claim in full. btclib-node's
  `submitblock` answers `null` for the first block of the reorg's fork,
  which ties the tip's work, where Core answers `inconclusive`
  ([ISS btclib-node#2419](https://github.com/btclib-org/btclib-node/issues/2419)).
- `mempool_accept.py`: Core's claim in full.
- `mempool_cluster.py`: Core's claim in full. Skips on `limit_cluster_size`
  ([ISS btclib-node#1383](https://github.com/btclib-org/btclib-node/issues/1383)).
- `feature_minchainwork.py`: Core's claim in full. Skips on `minimum_chain_work`
  ([ISS btclib-node#1500](https://github.com/btclib-org/btclib-node/issues/1500)).
  Core's `test_outbound_insufficient_work_disconnect` (bitcoin/bitcoin#36426) is
  the (outbound) row, which also asks for `mine`, `typed_outbound` and
  `debug_log`.
- `mempool_packages.py`: Core's claim in full. Skips on
  `limit_cluster_count`: btclib-node runs the cluster mempool, declaring
  `cluster_linearization`, and registers no `-limitclustercount` for the
  second node.
- `feature_versionbits_warning.py`: Core's claim. Skips on `alert_notify`
  ([ISS btclib-node#1475](https://github.com/btclib-org/btclib-node/issues/1475)).
- `p2p_headers_sync_with_minchainwork.py`: Core's claim in full. Skips on
  `minimum_chain_work`
  ([ISS btclib-node#1500](https://github.com/btclib-org/btclib-node/issues/1500)).
  The abort step reads stderr through `NodeAdapter.wait_until_stopped`
  ([ISS 318](https://github.com/btclib-org/functional-tests/issues/318)).
- `p2p_unrequested_blocks.py`: Core's claim in full. Skips on
  `minimum_chain_work`
  ([ISS btclib-node#1500](https://github.com/btclib-org/btclib-node/issues/1500)).
- `p2p_1p1c_network.py`: Core's claim in full.
- `mempool_ephemeral_dust.py`: a row per subtest of Core's file. The
  nonzero-dust body needs `-minrelaytxfee`
  ([ISS btclib-node#1332](https://github.com/btclib-org/btclib-node/issues/1332)).
  In the reorg body, a mined parent with a dust output and a fee returns to
  the mempool
  ([ISS btclib-node#1594](https://github.com/btclib-org/btclib-node/issues/1594)).
- `feature_notifications.py`: a row per option. Skips on `block_notify` and
  `shutdown_notify`
  ([ISS btclib-node#1519](https://github.com/btclib-org/btclib-node/issues/1519)),
  `alert_notify`
  ([ISS btclib-node#1475](https://github.com/btclib-org/btclib-node/issues/1475)).
  `-walletnotify` is not ported:
  `tests/integration/feature_notifications_test.py`'s module docstring says
  why.
- `feature_settings.py`: Core's claim in full. Skips on `settings_file`
  ([ISS btclib-node#1523](https://github.com/btclib-org/btclib-node/issues/1523)).
- `feature_assumevalid.py`: Core's claim in full. Skips on `assume_valid`
  ([ISS btclib-node#1576](https://github.com/btclib-org/btclib-node/issues/1576)).
- `feature_rbf.py`: Core's claim in full. Skips on `incremental_relay_fee`
  ([ISS btclib-node#1596](https://github.com/btclib-org/btclib-node/issues/1596)).
- `p2p_permissions.py`: Core's claim in full. The `forcerelay` body skips on
  `debug_log`; the others pass.
- `feature_shutdown.py`: has no row
  ([ISS 317](https://github.com/btclib-org/functional-tests/issues/317)).
  Its subject is the exit of a node asked to `stop` over RPC, which
  `NodeAdapter.wait_until_stopped` reads
  ([ISS 318](https://github.com/btclib-org/functional-tests/issues/318));
  `NodeAdapter.stop` sends `SIGTERM` and cannot tell them apart.
- `rpc_getchaintips.py`: Core's claim in full.
- `rpc_preciousblock.py`: Core's claim in full.
- `rpc_invalidateblock.py`: Core's claim in full.
- `rpc_signmessagewithprivkey.py`: Core's claim in full.
- `feature_chain_tiebreaks.py`: Core's claim in full.
- `p2p_sendheaders.py`: Core's claim in full.
- `p2p_fingerprint.py`: Core's claim in full.
- `rpc_estimatefee.py`: Core's claim in full.
- `p2p_nobloomfilter_messages.py`: the filtered-block step alone, a wire row
  and a log row ([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)).
  The node disconnects the peer that asks it for a filtered block, as Core
  does without `NODE_BLOOM`.
- `interface_http.py`: the excessive request size step alone, a wire row and a
  log row ([ISS 5](https://github.com/btclib-org/functional-tests/issues/5)).
- `feature_block.py`: a smaller claim: Core's sequence without its
  invalid-transaction templates and its `LARGE_REORG_SIZE` re-org
  ([ISS 439](https://github.com/btclib-org/functional-tests/issues/439)),
  as a body per row, wire and log. Skips on `peer_timeout`.
- `feature_prune_stale_fork.py`: Core's claim in full. Skips on `fastprune`:
  `cli.py` registers no `-fastprune`.
- `rpc_setban.py`: a row per section of Core's file. Skips on `ban` where the
  build keeps no ban list
  ([ISS btclib-node#1088](https://github.com/btclib-org/btclib-node/issues/1088)).
  The restart row skips on `debug_log`; the ban, `-bantime`, noban
  ([ISS btclib-node#1320](https://github.com/btclib-org/btclib-node/issues/1320))
  and non-IP
  ([ISS btclib-node#1218](https://github.com/btclib-org/btclib-node/issues/1218))
  rows pass.
- `feature_utxo_set_hash.py`: Core's claim without its hard-coded hashes. A
  build whose bare `gettxoutsetinfo` answers no `hash_serialized_3` fails it;
  one past
  [ISS btclib-node#1598](https://github.com/btclib-org/btclib-node/issues/1598)
  passes.
- `p2p_eviction.py`: Core's claim in full. Skips on `inbound_eviction` where
  the build does not evict
  ([ISS btclib-node#1064](https://github.com/btclib-org/btclib-node/issues/1064)),
  and passes on a build past
  [ISS btclib-node#1179](https://github.com/btclib-org/btclib-node/issues/1179).
- `p2p_feefilter.py`: the first row, the `forcerelay` row
  ([ISS btclib-node#1320](https://github.com/btclib-org/btclib-node/issues/1320)),
  the filtering row and the block-relay-only row pass. The `-blocksonly` row
  skips on `blocks_only`.
- `p2p_tx_download.py`: the spurious-`notfound`, disconnect, `notfound` and
  large-inv rows pass on a build past
  [ISS btclib-node#1196](https://github.com/btclib-org/btclib-node/issues/1196)
  and
  [ISS btclib-node#1320](https://github.com/btclib-org/btclib-node/issues/1320).
  Every other row passes on a build declaring `Capability.CLOCK`, or
  skips on the capability its cell names.
- `p2p_blocksonly.py`: the `-blocksonly` rows skip on `blocks_only`, and the
  block-relay-only log row on `debug_log`.
- `p2p_orphan_handling.py`: every row skips on `orphanage` on a build that
  serves no `getorphantxs`
  ([ISS btclib-node#1420](https://github.com/btclib-org/btclib-node/issues/1420)).
  On one that does, each **skip** row skips on a later capability.
- `p2p_compactblocks.py`: the log rows skip. The optimistic
  invalid-`cmpctblock` row fails: the node keeps the peer, as the pinned
  bitcoind does, where Core's `master` drops it
  ([ISS btclib-node#2170](https://github.com/btclib-org/btclib-node/issues/2170)).
  Every other row passes: a `cmpctblock` from a peer that sent no
  `sendcmpct` is ignored
  ([ISS btclib-node#1890](https://github.com/btclib-org/btclib-node/issues/1890),
  [ISS btclib-node#1897](https://github.com/btclib-org/btclib-node/issues/1897));
  a block is received as a `cmpctblock` and a high-bandwidth peer selected
  ([ISS btclib-node#1321](https://github.com/btclib-org/btclib-node/issues/1321));
  a new block is announced as a `cmpctblock`, and `getpeerinfo` reports a
  peer asking for that
  ([ISS btclib-node#1223](https://github.com/btclib-org/btclib-node/issues/1223));
  `getchaintips` is answered
  ([ISS btclib-node#1393](https://github.com/btclib-org/btclib-node/issues/1393));
  and the invalid-`sendcmpct` and empty-`getblocktxn` wire rows drop the peer
  Core's `master` drops
  ([ISS btclib-node#1451](https://github.com/btclib-org/btclib-node/issues/1451),
  [ISS btclib-node#1450](https://github.com/btclib-org/btclib-node/issues/1450)).
- `p2p_opportunistic_1p1c.py`: every row skips, on `orphanage` where the build
  serves no `getorphantxs`
  ([ISS btclib-node#1420](https://github.com/btclib-org/btclib-node/issues/1420))
  and on a later capability where it does.
- `p2p_block_times.py`: fails: `getpeerinfo` answers no
  `last_block_announcement`, which Core's `master` answers
  ([ISS btclib-node#2171](https://github.com/btclib-org/btclib-node/issues/2171)).
- `tool_utxo_to_sqlite.py`: has no row. Its subject is
  `contrib/utxo-tools/utxo_to_sqlite.py`, a script of Core's source tree, not
  a node; what it asks of the node is `rpc_dumptxoutset.py`'s and
  `feature_utxo_set_hash.py`'s subject.
