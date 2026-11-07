# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""What a node can do, and how many tests skipped for lack of it.

Rule 4 of [ISS 2220](https://github.com/btclib-org/btclib/issues/2220): a
node declares its capabilities, a test needing one the node lacks skips,
and the run prints a skip count per capability -- a number the node's own
tracker can read, never a silent pass. A capability names what a node
*can do*, not how it spells the call that does it: `Capability.CONNECT`
covers what `node.connect_nodes` does, and `BtcdAdapter`, whose node
takes `addnode` with one argument fewer, declares the same member and
spells the call its own way in `dial`.

A fact that differs between two builds of one node is read from the
build under test, never fixed as a class-wide constant
([ISS functional-tests#35](https://github.com/btclib-org/functional-tests/issues/35)):
the class says what every build of that node can do, and the running
build says what this one can. Where the fact is whether a capability
is there at all, it is still a `Capability`, declared per *instance*
from a probe rather than fixed for the whole class:
`BtclibNodeAdapter.__init__`'s own `_writes_auth_cookie` decides, per
instance, whether `Capability.RPC_AUTH_CONFIG` is declared at all, and
`BitcoindAdapter.__init__`'s own `_has_wallet` narrows `Capability.MINE`
and `Capability.NODE_WALLET` off an instance built against a `bitcoind`
without wallet support, widening or narrowing the class's own frozen set
rather than replacing it outright. Where the fact is not whether a
capability exists but *how* a capability every build declares alike
behaves once exercised --
whether `bitcoind`'s own `ADD_ONION` negotiates BIP434's proof-of-work
defenses (`tests/integration/feature_torcontrol_bitcoind_test.py`),
which p2p protocol version a build speaks
(`tests/integration/p2p_bip434_feature_bitcoind_test.py`) -- there is no
skip to gate, so nothing is added to this enum: the test itself reads
the fact off the running node's own RPC or its own wire behaviour and
asserts whichever shape that build produces. The enum is for what
`SkipCounts.report`'s own count is over -- an instance either declares a
member or it does not, and a run either skips a test for lacking it or
does not -- and an expectation with no skip attached to it is not that.

`p2p_getdata` needs none of the members below: it asks only for what
every adapter provides unconditionally -- a running node, its RPC and
its p2p port -- so `require` is exercised by `tests/capability_test.py`
rather than by that test. `tests/integration/` is where the rest of the
members are exercised, `MINE`, `CONNECT` and `RAW_MESSAGE` among them.

`UA_COMMENT` is the first of the option family
([ISS functional-tests#3](https://github.com/btclib-org/functional-tests/issues/3),
whose own body already answers the design question this way), and it
sets the shape every later option takes: one member per Core option a
ported test actually asks for, added the moment that test is ported
rather than declared for the whole of Core's option surface up front,
which is large and mostly untouched by any test this suite has ported.
The rejected alternative is a single parameterized `Capability.OPTION`,
keyed on the option's own name, with one node-side mapping of names to
"has it"; rule 4's own count is what this file already prints one line
per member of, sorted by value, and a parameterized capability would
need to fold that back to one line itself rather than getting it from
`SkipCounts.report` unchanged. One member per option is what keeps a
name in the enum a name in the printed summary, at the cost this module
already carries: a member added by hand, per option, per test ported. A
member's own value is what that summary prints, so it is spelled the
same as the member rather than shortened on its own, keeping the
summary's own name for a capability the same as the code's.

Not every Core option a test names earns a member here. Step 5's own
charter carries the narrower rule first: "wallet and USDT tests stay
out" and "a test of bitcoind's own options runs against bitcoind alone".
The first rule's wallet half is overridden by
[ISS functional-tests#45](https://github.com/btclib-org/functional-tests/issues/45),
which brings Core's node-wallet tests in, one body over both nodes behind
`NODE_WALLET`, which `BitcoindAdapter` alone declares. An option only
bitcoind has a reason to carry -- `-disablewallet`, `-torcontrol` -- is
never declared or skipped by another node under this
mechanism; it is a bitcoind-only test's subject, a shape this module
does not build.
[ISS functional-tests#23](https://github.com/btclib-org/functional-tests/issues/23)
gives that shape a place of its own, and this paragraph is the one rule
that decides which option qualifies, rather than a decision made test by
test: a `*_bitcoind_test.py` module with no `*_btclib_node_test.py`
counterpart, asking `require` for nothing, is a bitcoind-only test.
`TF2.md`'s own per-test ledger spells such a row `bitcoind only` in its
`btclib-node` column rather than any `skip (...)` -- a cell nothing will
ever turn into a `pass` or a `fail`, unlike an ordinary skip.

**This module imports no test runner.** `pyproject.toml`'s own
`[project] dependencies` name no test runner (this
package's own `__init__.py` says so), and Core's own test framework
runs under no `pytest` at all -- objective 2 of
ISS btclib-org/btclib#2220 is that Core can adopt this suite, which a
hard runtime dependency on somebody else's test runner would work
against. `require` raises
`MissingCapabilityError`, its own exception, rather than calling
`pytest.skip`; `tests/conftest.py`'s own `pytest_runtest_call`
hookwrapper is what translates that into an actual skip, in the one
tree that ever runs these tests under pytest. A prior version imported
`pytest` here
directly, which `sphinx-build`'s own `autodoc` -- run from the `docs`
dependency group, which does not install `pytest` -- failed to import
with `ModuleNotFoundError: No module named 'pytest'`, cascading into
every module that imports this one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Set as AbstractSet
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

__all__ = [
    "Capability",
    "MissingCapabilityError",
    "SkipCounts",
    "require",
]


class MissingCapabilityError(Exception):
    """Raised by `require` when the node under test does not declare it.

    Not `pytest.skip.Exception`: this module's own docstring has why.
    """


class Capability(Enum):
    """What a test may ask a node adapter for, named by what it does.

    `MINE` -- produce a block and have the node accept it as its own new
    tip, however it gets there: `generatetoaddress` for a node with a
    wallet, a client-built block over `submitblock` for one without.
    `CONNECT` -- accept a second node of its own kind as a peer, the way
    `node.connect_nodes` dials and waits for one.
    `DISCONNECT` -- drop an already-connected peer on request, the way
    `node.disconnect_nodes` asks over `disconnectnode`. Not implied by
    `CONNECT`: `addnode` and `disconnectnode` are two different RPCs, and
    a node answering the first need not answer the second (measured of
    `btclib-node`, [ISS functional-tests#43](https://github.com/btclib-org/functional-tests/issues/43)'s
    own finding).
    `BAN` -- record and enforce a `setban`/`listbanned`/`clearbanned` ban
    list, the way `rpc_setban`'s own subject does: an address already
    connected drops the moment it is banned. Node-linking's own third
    capability, beside `CONNECT` and `DISCONNECT`
    ([ISS functional-tests#43](https://github.com/btclib-org/functional-tests/issues/43)).
    `RAW_MESSAGE` -- send an arbitrary p2p message to an already-connected
    peer, named by that peer's own index, the way Core's `sendmsgtopeer`
    does on the node under test's behalf; `p2p_net_deadlock`'s own
    subject needs a node that offers it, and today only bitcoind does.
    `BLK_FILES` -- write its chain to disk the way Core does, `blk*.dat`
    files under a `blocks/` directory that a caller may read directly:
    a fact the wire has no call for, unlike where the option that names
    the directory lives, which a node without this capability may still
    accept.
    `DEBUG_LOG` -- write a debug log a caller can read and match Core's
    own wording against, the way `assert_debug_log`
    (`debug_log.py`) does. The log family (the third of step 5's five,
    [ISS 5](https://github.com/btclib-org/functional-tests/issues/5))
    is what this names: where the fact an `assert_debug_log` call in
    Core asks about is also observable on the wire -- a disconnect,
    `getpeerinfo` -- the ported assertion reads the wire instead and
    needs no capability at all; where only the log carries it, a test
    needs this one. A node's own log is truthful about what *it* did,
    not about what Core would have called it, so a byte-for-byte match
    against Core's own wording is a fact only bitcoind's own binary can
    supply.
    `UA_COMMENT` -- append a caller-chosen comment to the subversion
    string `getnetworkinfo` reports, the fact Core's own `-uacomment`
    asks for. The first of the option family (rule 4,
    [ISS functional-tests#3](https://github.com/btclib-org/functional-tests/issues/3)).
    `CLOCK` -- accept a caller-set wall clock, Core's `setmocktime`
    (`test/functional/test_framework/test_node.py`'s own
    `TestNode.setmocktime`): every RPC and every p2p timeout this node
    reads the time through sees the caller's clock instead of the real
    one, until `0` is set to release it back.
    `RPC_AUTH_CONFIG` -- recognise `rpcauth`, `rpcwhitelist` and
    `rpcwhitelistdefault` written into `bitcoin.conf`, the way Core's
    own `rpc_users` and `rpc_whitelist` add a credential or restrict its
    RPC surface through the config file rather than the command line.
    The disk family's own second capability
    ([ISS functional-tests#7](https://github.com/btclib-org/functional-tests/issues/7)):
    the fact is `bitcoin.conf` itself, `datadir_path`'s own file, not a
    fact the wire has a call for.
    `RPC_AUTH_NEGATION` -- recognise `-norpcauth` on the command line,
    disabling every `-rpcauth` value given before it, the way Core's own
    `rpc_users` checks it. Not `RPC_AUTH_CONFIG` itself: a node can parse
    `-rpcauth` and still have no `-no<name>` negation of any kind, which
    is the case of some `btclib-node` builds and not others
    (`btclib_node.py`'s own module docstring names which), so a test
    asking for the negation needs its own capability rather than riding
    on the one for the value it negates.
    `TEST_ACTIVATION_HEIGHT` -- hold one buried soft fork's own deployment
    inactive until a caller-chosen height, Core's own debug-only
    `-testactivationheight=<deployment>@<height>`. Regtest's own chain
    parameters activate every buried deployment otherwise -- BIP34, BIP66,
    BIP65 and CSV from height 1, segwit from genesis
    (`src/kernel/chainparams.cpp`'s own comment on each, "Always active
    unless overridden", at bitcoin/bitcoin@f0de87f040), so this is what lets
    a test hold one of them back long enough to observe the boundary at all
    ([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)).
    `V2TRANSPORT` -- accept BIP324 v2 connections, the way Core's own
    `-v2transport` does: `getpeerinfo`'s `transport_protocol_type` reads
    `v2` on such a connection, from another node of this kind or from a
    `Peer` (`peer.py`) asked for v2 -- issues
    [functional-tests#36](https://github.com/btclib-org/functional-tests/issues/36)
    and [functional-tests#175](https://github.com/btclib-org/functional-tests/issues/175).
    `DATACARRIER` -- recognise `-datacarrier` and `-datacarriersize`,
    Core's own pair of relay-policy knobs for an `OP_RETURN` output: the
    first turns its relay on or off, the second bounds how large one may
    be. One member for the pair rather than two: neither flag is ever
    tested apart from the other in
    [ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)'s
    own `mempool_datacarrier.py`, both landing at once.
    `PERMIT_BARE_MULTISIG` -- recognise `-permitbaremultisig`, Core's own
    switch for whether a bare `OP_CHECKMULTISIG` output is relayed at
    all, checked apart from `DUST_RELAY_FEE` because a test can ask for
    either alone.
    `DUST_RELAY_FEE` -- recognise `-dustrelayfee`, Core's own per-kilobyte
    rate an output's own value is measured against to call it dust.
    `BYTES_PER_SIGOP` -- recognise `-bytespersigop`, Core's own
    conversion rate from a sigop to the virtual bytes a transaction's own
    mempool footprint is billed for.
    `LIMIT_CLUSTER_COUNT` -- recognise `-limitclustercount`, Core's own
    cap on how many transactions, in-mempool and in-package together, one
    mempool cluster may hold
    ([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)'s
    own `mempool_package_limits.py`). Checked apart from
    `LIMIT_CLUSTER_SIZE`: a test can ask for either alone.
    `LIMIT_CLUSTER_SIZE` -- recognise `-limitclustersize`, Core's own cap
    on one cluster's own total virtual size
    (`mempool_updatefromblock.py`).
    `MAXMEMPOOL` -- recognise `-maxmempool`, Core's own cap, in megabytes,
    on the mempool's own total size -- what
    `mempool_util.fill_mempool` needs a node started small enough under
    to reach eviction at all
    ([ISS functional-tests#70](https://github.com/btclib-org/functional-tests/issues/70)).
    `DESCRIPTOR_ACTIVITY` -- answer `getdescriptoractivity`, Core's own
    RPC pairing spend and receive events with the descriptors and blocks
    a caller names. Named for the RPC rather than for an option, the way
    `MINE`/`CONNECT`/`DISCONNECT`/`BAN`/`RAW_MESSAGE` already are: no
    flag gates it, so what a node either answers or does not is the
    method itself
    ([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)).
    `BLOCK_STATS` -- answer `getblockstats`, Core's own per-block
    statistics RPC, named the same way and for the same reason.
    `FASTPRUNE` -- recognise `-fastprune`, Core's own debug-only switch
    to block files far smaller than a real node's, so that a test reaches
    a block file's size limit with a single large block
    ([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)'s
    own `feature_fastprune.py`).
    `INBOUND_EVICTION` -- make room for a new inbound peer, once its
    inbound slots are full, by disconnecting an existing one that none of
    Core's own protections covers, the slots being what `-maxconnections`
    bounds (`p2p_eviction.py`). Named for the eviction rather than for
    the option: a node can accept `-maxconnections` and refuse the new
    peer instead of evicting an old one.
    `BLOCK_FILTER_INDEX` -- keep BIP158's basic block filter for every
    block once `-blockfilterindex` asks for it, and answer `scanblocks`
    (`rpc_scanblocks.py`) and `getblockfilter` (`rpc_getblockfilter.py`)
    from that index.
    `VALIDATE_ADDRESS` -- answer `validateaddress`, Core's own RPC
    decoding an address for the chain the node runs: the `scriptPubKey`
    a valid one decodes to, and the error and `error_locations` an
    invalid one earns (`rpc_validateaddress.py`). Named for the RPC, as
    `BLOCK_STATS` is.
    `TYPED_OUTBOUND` -- dial an address the caller names as the outbound
    connection type the caller chooses, `outbound-full-relay`,
    `block-relay-only`, `addr-fetch` or `feeler`, the way Core's
    `TestNode.add_outbound_p2p_connection` asks `addconnection` to
    ([ISS functional-tests#44](https://github.com/btclib-org/functional-tests/issues/44)).
    Not `CONNECT`: `addnode` dials a manual connection, one type only.
    `NodeAdapter.add_outbound_connection` (`node.py`) is the call, and
    `peer.Listener` what a test has the node dial.
    `RPC_WORK_QUEUE` -- serve RPC on as few worker threads as a caller
    names, queueing at most as many requests as it names for them and
    refusing the rest, the facts Core's own `-rpcthreads` and
    `-rpcworkqueue` set (`rpc_echo_payload.py`). One member for the pair,
    as `DATACARRIER` is: the one ported test asking for either sets both.
    `BLOCKS_ONLY` -- recognise `-blocksonly`, Core's own switch to a node
    relaying no transactions, which also selects no BIP152 high-bandwidth
    peer and asks for a full block rather than a compact one
    (`p2p_compactblocks_blocksonly.py`).
    `BLOCK_FROM_PEER` -- answer `getblockfrompeer`, Core's own RPC asking a
    named peer for a block whose header the node already has
    (`rpc_getblockfrompeer.py`). Named for the RPC, as `BLOCK_STATS` is.
    `ACCEPT_NON_STANDARD` -- admit to its mempool, on request, a
    transaction its standardness rules refuse while its script checks
    still run, the fact Core's own `-acceptnonstdtxn` sets
    (`feature_cltv.py`, whose spends prepend opcodes to a scriptSig).
    `SUSPEND_NETWORK` -- stop all p2p activity on request, dropping every
    peer, and resume it on request, the way Core's own `setnetworkactive`
    does (`p2p_node_network_limited.py`).
    `DESCRIPTOR_INFO` -- answer `getdescriptorinfo`, Core's own RPC
    analysing an output descriptor: its checksummed form, the
    single-path descriptors a multipath one expands to, whether it is
    ranged, solvable and carries a private key, and the error a
    malformed one earns (`rpc_getdescriptorinfo.py`). Named for the RPC,
    as `BLOCK_STATS` is.
    `PEER_TIMEOUT` -- recognise `-peertimeout`, Core's own bound on how
    long a new connection may go without finishing its handshake, and
    before which no inactivity check reaches it at all
    (`p2p_timeouts.py`, `p2p_ping.py`).
    `MEMPOOL_EXPIRY` -- recognise `-mempoolexpiry`, Core's own age, in
    hours, past which a transaction leaves the mempool
    (`mempool_expiry.py`).
    `SIGN_RAW_TRANSACTION` -- sign a raw transaction's inputs with private
    keys the caller hands it, and merge copies of one transaction each
    carrying some of the signatures into one: Core's own
    `signrawtransactionwithkey` and `combinerawtransaction`, which need no
    wallet (`rpc_createmultisig.py`'s `do_multisig`,
    `rpc_signrawtransactionwithkey.py`). One member for the pair, as
    `DATACARRIER` is.
    `INVALIDATE_BLOCK` -- mark a block invalid on request, and go back to
    the best chain not holding it, Core's own `invalidateblock`
    (`feature_csv_activation.py`, which takes each accepted block back
    off); and take that mark off a block, its ancestors and its
    descendants on request, going back to the best chain again, Core's
    own `reconsiderblock` (`rpc_invalidateblock.py`). Named for the
    first RPC, as `BLOCK_STATS` is, and one member for the pair, as
    `SIGN_RAW_TRANSACTION` is.
    `REINDEX_AFTER_FAILURE` -- refuse to start over a block index missing
    from `blocks/index`, advising a reindex, and reindex from its block
    files instead on a start given Core's own debug-only
    `-test=reindex_after_failure_noninteractive_yes`
    (`feature_reindex_init.py`).
    `GENERATE` -- build and solve a block itself, on request, paying the
    output an address or a descriptor names and carrying the
    transactions the caller names, and take it as its new tip: Core's
    own `generatetoaddress` and `generateblock` (`rpc_generate.py`).
    The `help` of Core's hidden `generate`, naming the `-generate`
    option that replaces it, is a check that test makes, not part of
    the capability. Not `MINE`: a node can take a block a client built
    over `submitblock`, which `MINE` names, and build none itself.
    `SCAN_UTXO_SET` -- search its own UTXO set for the outputs a list of
    descriptors matches, Core's own `scantxoutset`
    (`rpc_scantxoutset.py`). Named for what it reads rather than for the
    RPC's own spelling.
    `PROXY` -- dial a peer through the SOCKS5 proxy `-proxy` names, on a
    TCP address or a `unix:` socket path, for every network, an onion one
    through `-onion`'s where that is given, sending a proxy that accepts
    username/password credentials of each connection's own under
    `-proxyrandomize`; report each network's proxy in `getnetworkinfo`;
    and refuse to start on a `-proxy` or `-onion` naming no usable proxy
    (`feature_proxy.py`,
    [ISS functional-tests#47](https://github.com/btclib-org/functional-tests/issues/47)).
    One member for the three options, as `DATACARRIER` is for its pair:
    `-onion` names a SOCKS5 proxy as `-proxy` does, for onion alone, and
    `-proxyrandomize` qualifies whichever of the two is given.
    `PROXY_PER_NETWORK` -- take `-proxy`'s `=<network>` suffix, setting
    the proxy of that network alone or, given `0`, removing it, and refuse
    to start on a suffix that is empty or names an unknown network
    (`feature_proxy.py`). Not `PROXY`'s own: a node can take `-proxy` and
    read the suffix as part of the port.
    `CJDNS` -- take a CJDNS address, one in `fc00::/8`, as CJDNS once
    `-cjdnsreachable` says the network is reachable, dial it through the
    proxy `-proxy` names, and report CJDNS reachable in `getnetworkinfo`
    (`feature_proxy.py`).
    `I2P_SAM` -- recognise `-i2psam`, the I2P router's SAM endpoint, and
    `-i2pacceptincoming`, report that endpoint as I2P's proxy in
    `getnetworkinfo` with I2P reachable, and refuse to start on one naming
    no usable address (`feature_proxy.py`); dial an I2P address through
    that endpoint on port 0 alone, refusing any other port
    (`p2p_i2p_ports.py`), over a SAM session whose key it keeps on disk
    where `-i2pacceptincoming` asks it to accept I2P connections, and over
    one with a key of its own, never saved, where it does not
    (`p2p_i2p_sessions.py`). Named for the SAM bridge rather than for I2P:
    no ported test has an I2P router answer at the endpoint.
    `ONLYNET` -- recognise `-onlynet`, Core's own restriction of outbound
    connections to the networks it names, refusing to start on a network
    it does not know or on one it has no way to reach
    (`feature_proxy.py`).
    `NODE_WALLET` -- hold wallets of its own and serve Core's wallet RPCs
    over them: `createwallet` at the node's own endpoint, and every method
    a wallet answers -- `getnewaddress`, `signmessage`, `importdescriptors`,
    `listtransactions` among them -- at `/wallet/<name>`, the endpoint
    `bitcoin_core_rpc`'s own `for_wallet` addresses. The subject of Core's
    own `wallet_*.py`, which drive bitcoind's built-in wallet
    ([ISS functional-tests#45](https://github.com/btclib-org/functional-tests/issues/45)).
    Not `MINE`: that names a block the node accepts as its tip, which a
    node without a wallet still offers over `submitblock`, where this
    names the wallet itself. A wallet a separate program keeps for a node
    is not this member either
    ([ISS functional-tests#199](https://github.com/btclib-org/functional-tests/issues/199)).
    `TX_RECONCILIATION` -- offer and accept BIP330's transaction
    reconciliation once `-txreconciliation` asks for it, Core's own
    switch: `sendtxrcncl` sent to a peer that relays transactions, and a
    peer's own `sendtxrcncl` registered (`p2p_sendtxrcncl.py`).
    `PEER_BLOOM_FILTERS` -- recognise `-peerbloomfilters`, Core's own
    switch to serving BIP37 bloom filters, which offers `NODE_BLOOM` in
    the node's own `version` (`p2p_sendtxrcncl.py`).
    `REINDEX` -- rebuild its block index and its chainstate from the
    block files it already stored, on a start given Core's own
    `-reindex`, and its chainstate alone on one given
    `-reindex-chainstate` (`feature_reindex.py`,
    `feature_reindex_readonly.py`). One member for the pair, as
    `DATACARRIER` is: `feature_reindex.py` alternates them on one node.
    `CAPTURE_MESSAGES` -- write every p2p message it sends a peer and every
    one it processes from that peer to files of that peer's own, under
    the chain directory's `message_capture/`, once Core's own debug-only
    `-capturemessages` asks for it (`p2p_message_capture.py`).
    `BLOCKS_XOR` -- recognise `-blocksxor`, Core's own switch for whether
    the block and undo files are obfuscated with the key `blocks/xor.dat`
    holds, and refuse a start disabling it where the stored key is not
    all zeros (`feature_blocksxor.py`).
    `ORPHANAGE` -- keep a transaction a peer sent whose inputs it cannot
    find yet, admit it to the mempool once its parent arrives, with that
    parent where the parent pays too little alone and the two pay enough
    together, Core's 1p1c (`p2p_opportunistic_1p1c.py`,
    `p2p_1p1c_network.py`), and report what it keeps, with the peers that
    announced each, over Core's own `getorphantxs` (`rpc_orphans.py`).
    Named for what it keeps rather than for the RPC's own spelling, as
    `SCAN_UTXO_SET` is.
    `BLOCK_PROPOSAL` -- check a block a client proposes on top of its own
    tip without storing it or asking for its proof-of-work, and answer
    `null` where it is valid or BIP22's reason for the first rule it
    breaks: Core's own `getblocktemplate` in BIP23's `proposal` mode
    (`mining_template_verification.py`). Not `MINE`: a node can take a
    client's block over `submitblock` and check none it is not asked to
    store.
    `DNS_SEED` -- ask its chain's DNS seeds for peer addresses once it
    starts, the way Core's own `-dnsseed` does: by default, but not where
    `-connect` names its peers unless `-dnsseed` asks for it; at once
    under `-forcednsseed`, which it refuses beside `-dnsseed` off; and
    otherwise, with addresses already known, only after a wait, longer
    where it knows many, and not at all where enough outbound full-relay
    peers connect during it, block-relay-only ones not counting
    (`p2p_dns_seeds.py`).
    `ADDRESS_FETCH` -- dial the addresses it keeps on its own, and ask a
    node a caller names for more, the way Core's own `-seednode` does: at
    once where it keeps no address, and otherwise only once a wait passes
    without enough outbound full-relay peers (`p2p_seednode.py`).
    `KNOWN_ADDRESSES` -- take a peer's address a caller hands it into the
    addresses it keeps for finding peers, and list those back: Core's own
    test-only `addpeeraddress` and its `getnodeaddresses`
    (`p2p_dns_seeds.py`, `p2p_addr_selfannouncement.py`, `p2p_seednode.py`).
    One member for the table's writer and its reader, as `BAN` is for its
    ban list's.
    `EXTERNAL_IP` -- recognise `-externalip`, Core's own option naming an
    address the node advertises to its peers as its own
    (`p2p_addr_selfannouncement.py`).
    `PRIVATE_BROADCAST` -- send a transaction submitted over
    `sendrawtransaction` to peers over short-lived connections of its own
    through Tor or I2P, without putting it in its mempool first, until a
    peer sends it back; list what it is sending, and stop sending one on
    request; and send one again once it goes stale: Core's own
    `-privatebroadcast`, `getprivatebroadcastinfo` and
    `abortprivatebroadcast`, with the `mockscheduler` that moves a stale
    transaction's resend forward (`p2p_private_broadcast.py`).
    `STARTUP_NOTIFY` -- run a shell command a caller names once it has
    started, the way Core's own `-startupnotify` does
    (`feature_startupnotify.py`).
    `DUMP_UTXO_SET` -- write its own UTXO set to a file a caller names, at
    its tip or rolled back to an earlier block of its own chain, Core's
    own `dumptxoutset` (`rpc_dumptxoutset.py`). Named for what it writes
    rather than for the RPC's own spelling, as `SCAN_UTXO_SET` is.
    `LOAD_BLOCK` -- import, on starting, the blocks a file a caller names
    holds, each record the network's message start, the block's length
    little-endian and the block, the way Core's own `-loadblock` does
    (`feature_loadblock.py`).
    `LISTEN_ADDRESS` -- listen for peers where a caller's own options
    say rather than where its adapter binds it, the way Core's own
    `-port` and `-bind` do: on every address at the port the last
    `-port` names, and on `127.0.0.1` at the port after it for Tor's
    inbound connections; only on the addresses `-bind` names where one
    is given, a bind naming no port taking `-port`'s, or the port after
    it where the bind is an onion one; and refusing to start on a
    `-port` outside 1 to 65535 (`feature_port.py`).
    `MAX_TIP_AGE` -- recognise `-maxtipage`, Core's own bound, in seconds,
    on how old its tip may be, by its own clock, for the node to leave
    initial block download (`feature_maxtipage.py`).
    `PEER_BLOCK_FILTERS` -- serve BIP157's compact block filters to peers
    once `-peerblockfilters` asks for it, Core's own switch, and not
    otherwise: signal `NODE_COMPACT_FILTERS` and answer `getcfilters`,
    `getcfheaders` and `getcfcheckpt` where it is on, and drop a peer
    asking for any of them where it is off (`p2p_blockfilters.py`). Beside
    `BLOCK_FILTER_INDEX`, which keeps the filters this serves: a node can
    keep them for its own RPC and serve none.
    `RPC_INFO` -- answer `getrpcinfo`, Core's own RPC listing the calls it
    is running, each with how long it has run, and the full path of its
    debug log (`interface_rpc.py`). Named for the RPC, as `BLOCK_STATS`
    is.
    `MIN_RELAY_TX_FEE` -- recognise `-minrelaytxfee`, Core's own rate, in
    BTC/kvB, under which a fee counts as zero for relay, and the floor of
    every `feefilter` the node sends a peer (`p2p_ibd_txrelay.py`).
    `CLUSTER_LINEARIZATION` -- keep its mempool grouped into clusters, the
    transactions its spends connect, each ordered into chunks of falling
    fee rate, and report them: Core's own `getmempoolcluster`, its hidden
    `getmempoolfeeratediagram` and `getmempoolinfo`'s own `optimal`
    (`mempool_cluster.py`). Named for what it keeps rather than for an
    RPC's own spelling, as `ORPHANAGE` is.
    `CLUSTER_REPLACEMENT` -- count the conflicts of a replacement by the
    clusters they are in, and refuse one reaching more than Core's 100 of
    them, as `ReplacementChecks` does from the cluster mempool on
    (`mempool_package_rbf.py`). A build without it counts the transactions
    replaced instead.
    `MINIMUM_CHAIN_WORK` -- recognise `-minimumchainwork`, Core's own
    debug-only floor, in hex, on chain work: a node whose tip is below it
    stays in initial block download and answers `getheaders` empty to a
    peer not holding the `download` permission, which `noban` implies,
    it downloads no block from a peer whose best known block is below it,
    and a value that is not hex, or that runs past 64 hex digits once an
    optional `0x` is dropped, refuses the start
    (`feature_minchainwork.py`); it stores no header of a block a peer
    sends unasked on a chain below it (`p2p_unrequested_blocks.py`).
    `MEMPOOL_GRAPH` -- walk the spends connecting its mempool's
    transactions and report them: every mempool transaction a given one
    descends from and every one descending from it, bare or with each
    one's own entry, Core's own `getmempoolancestors` and
    `getmempooldescendants`, and the mempool transaction spending a given
    output, its `gettxspendingprevout` with no index behind it
    (`mempool_packages.py`). Named for what it walks rather than for an
    RPC's own spelling, as `SCAN_UTXO_SET` is.
    `ALERT_NOTIFY` -- run a shell command a caller names whenever it
    raises an alert, the alert's message in place of the command's `%s`,
    the way Core's own `-alertnotify` does
    (`feature_versionbits_warning.py`).
    `PACKAGE_ACCEPTANCE` -- evaluate a package a client hands it, a child
    with its unconfirmed parents, as one against its mempool and take it
    in: a parent paying too little alone entering with the child paying
    for it, and a package replacing what its parent conflicts with where
    it pays for that under package RBF's own rules, each refusal answered
    with Core's own reason. Core's own `submitpackage`, and its
    `testmempoolaccept` handed a package it evaluates as one, answering
    a `package-error` for the whole (`mempool_package_rbf.py`). A package
    a peer relays, Core's 1p1c, is not this capability's. Named for what
    it takes rather than for an RPC's own spelling, as `ORPHANAGE` is.
    `BLOCK_NOTIFY` -- run a shell command a caller names whenever its tip
    changes outside initial block download, the new tip's hash in place of
    the command's `%s`, the way Core's own `-blocknotify` does
    (`feature_notifications.py`).
    `SHUTDOWN_NOTIFY` -- run a shell command a caller names once it
    begins shutting down, the way Core's own `-shutdownnotify` does
    (`feature_notifications.py`).
    `SETTINGS_FILE` -- keep a settings file in its chain's data directory
    the way Core's own `settings.json` is kept: written at start with a
    `_warning_` key, each value logged at the next start and left in the
    file at shutdown, a file that is not valid JSON, not a JSON object or
    that holds a key twice refusing the start, `-nosettings` on the
    command line or in `bitcoin.conf` turning it off, and
    `-settings=<path>` naming another file (`feature_settings.py`).
    `PRECIOUS_BLOCK` -- take a block a caller names as if it had been
    received before every other block of the same work, moving its tip to
    that block where the two tie, until a later call names another or the
    node restarts: Core's own `preciousblock` (`rpc_preciousblock.py`).
    Named for the RPC, as `BLOCK_STATS` is.
    `SIGN_MESSAGE_WITH_PRIVKEY` -- sign a message with a private key the
    caller hands it, and verify a message's signature under a P2PKH
    address: Core's own `signmessagewithprivkey` and `verifymessage`,
    which need no wallet (`rpc_signmessagewithprivkey.py`,
    `wallet_signmessagewithaddress.py`). Named for the
    first RPC, as `BLOCK_STATS` is, and one member for the pair, as
    `SIGN_RAW_TRANSACTION` is.
    `ESTIMATE_SMART_FEE` -- answer a fee rate estimate for a confirmation
    target, and refuse a malformed request for one with Core's own codes
    and messages: Core's own `estimatesmartfee` and `estimaterawfee`
    (`rpc_estimatefee.py`). Named for the first RPC, as `BLOCK_STATS` is,
    and one member for the pair, as `SIGN_RAW_TRANSACTION` is.
    `CHAIN_TIPS` -- report every tip of the blocks and headers it knows,
    each with its height, how far it branches off the active chain and a
    status, `active`, `valid-fork`, `headers-only` or `invalid` among
    them: Core's own `getchaintips` (`rpc_getchaintips.py`). Named for the
    RPC, as `BLOCK_STATS` is.
    `ASSUME_VALID` -- recognise `-assumevalid=<hash>`, Core's own hex hash
    of a block externally verified valid. A block skips script
    verification where every one of these holds: the hash is not all
    zeros, it names a block the node already has a header for, the
    candidate is on the chain leading to that block, the candidate is
    also on the chain leading to the node's own best header, the best
    header's chain work is at least `-minimumchainwork`, and the
    candidate is buried under more than two weeks' worth of work of the
    best header; a candidate failing any of those is verified in full
    (`feature_assumevalid.py`).
    `INCREMENTAL_RELAY_FEE` -- recognise `-incrementalrelayfee`, Core's own
    rate, in BTC/kvB, that a replacement must add to the fee of what it
    replaces, per virtual byte of its own size (`feature_rbf.py`). Where
    `-minrelaytxfee` is not given, Core raises the minimum relay fee rate
    to it.
    `PEER_PERMISSIONS` -- grant the permissions Core's own `-whitelist` and
    `-whitebind` name to the peers at an address or on a bind, and list
    them under `permissions` in `getpeerinfo`: the defaults of a bare
    address, the flags a bare permission list replaces them with, `all`,
    the merge of a whitelisted address with a whitebind's own flags, the
    `in` and `out` directions, and the start refused on a malformed
    list (`p2p_permissions.py`).
    `BLOCKS_DIR` -- recognise `-blocksdir`, Core's own option naming the
    directory that holds the block files in place of the data directory,
    and refuse a start naming one that does not exist
    (`feature_blocksdir.py`, `feature_filelock.py`). Not `BLK_FILES`,
    which is the files themselves: a node can take the option and keep
    its chain in another format.
    `INCLUDE_CONF` -- read `includeconf` lines of its configuration file,
    refuse `-includeconf` on the command line, and warn about one in an
    included file (`feature_includeconf.py`).
    `UTXO_SET_INFO` -- answer `gettxoutsetinfo`, Core's own RPC reporting
    the commitments to the UTXO set, `muhash` and `hash_serialized_3`
    among them (`feature_utxo_set_hash.py`). Named for the RPC, as
    `BLOCK_STATS` is.
    `MEMPOOL_ENTRY` -- answer `getmempoolentry`, Core's own RPC reporting
    one mempool transaction's size, fees, `wtxid` and ancestors
    (`mempool_accept_wtxid.py`, `mempool_updatefromblock.py`). Named for
    the RPC, as `BLOCK_STATS` is.
    `ADDRESS_TABLES` -- list the new and tried tables of the addresses it
    keeps, each entry under its bucket and position, and count each
    table's entries per network: Core's own `getrawaddrman` and
    `getaddrmaninfo`; place the addresses deterministically under Core's
    own `-test=addrman`, and check the tables' consistency every `<n>`
    operations under its `-checkaddrman=<n>`, both debug-only options
    (`rpc_net.py`). Not `KNOWN_ADDRESSES`, which hands an address in and
    lists it back: a node can do that over tables of another shape. One
    member for the four, as `PROXY` is for its three options:
    `rpc_net.py`'s `test_addpeeraddress` uses them together.
    """

    MINE = "mine"
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    BAN = "ban"
    RAW_MESSAGE = "raw_message"
    BLK_FILES = "blk_files"
    DEBUG_LOG = "debug_log"
    UA_COMMENT = "ua_comment"
    CLOCK = "clock"
    RPC_AUTH_CONFIG = "rpc_auth_config"
    RPC_AUTH_NEGATION = "rpc_auth_negation"
    TEST_ACTIVATION_HEIGHT = "test_activation_height"
    V2TRANSPORT = "v2transport"
    DATACARRIER = "datacarrier"
    PERMIT_BARE_MULTISIG = "permit_bare_multisig"
    DUST_RELAY_FEE = "dust_relay_fee"
    BYTES_PER_SIGOP = "bytes_per_sigop"
    LIMIT_CLUSTER_COUNT = "limit_cluster_count"
    LIMIT_CLUSTER_SIZE = "limit_cluster_size"
    MAXMEMPOOL = "maxmempool"
    DESCRIPTOR_ACTIVITY = "descriptor_activity"
    BLOCK_STATS = "block_stats"
    FASTPRUNE = "fastprune"
    INBOUND_EVICTION = "inbound_eviction"
    BLOCK_FILTER_INDEX = "block_filter_index"
    VALIDATE_ADDRESS = "validate_address"
    TYPED_OUTBOUND = "typed_outbound"
    RPC_WORK_QUEUE = "rpc_work_queue"
    BLOCKS_ONLY = "blocks_only"
    BLOCK_FROM_PEER = "block_from_peer"
    ACCEPT_NON_STANDARD = "accept_non_standard"
    SUSPEND_NETWORK = "suspend_network"
    DESCRIPTOR_INFO = "descriptor_info"
    PEER_TIMEOUT = "peer_timeout"
    MEMPOOL_EXPIRY = "mempool_expiry"
    SIGN_RAW_TRANSACTION = "sign_raw_transaction"
    INVALIDATE_BLOCK = "invalidate_block"
    REINDEX_AFTER_FAILURE = "reindex_after_failure"
    GENERATE = "generate"
    SCAN_UTXO_SET = "scan_utxo_set"
    PROXY = "proxy"
    PROXY_PER_NETWORK = "proxy_per_network"
    CJDNS = "cjdns"
    I2P_SAM = "i2p_sam"
    ONLYNET = "onlynet"
    NODE_WALLET = "node_wallet"
    TX_RECONCILIATION = "tx_reconciliation"
    PEER_BLOOM_FILTERS = "peer_bloom_filters"
    REINDEX = "reindex"
    CAPTURE_MESSAGES = "capture_messages"
    BLOCKS_XOR = "blocks_xor"
    ORPHANAGE = "orphanage"
    BLOCK_PROPOSAL = "block_proposal"
    DNS_SEED = "dns_seed"
    ADDRESS_FETCH = "address_fetch"
    KNOWN_ADDRESSES = "known_addresses"
    EXTERNAL_IP = "external_ip"
    PRIVATE_BROADCAST = "private_broadcast"
    STARTUP_NOTIFY = "startup_notify"
    DUMP_UTXO_SET = "dump_utxo_set"
    LOAD_BLOCK = "load_block"
    LISTEN_ADDRESS = "listen_address"
    MAX_TIP_AGE = "max_tip_age"
    PEER_BLOCK_FILTERS = "peer_block_filters"
    RPC_INFO = "rpc_info"
    MIN_RELAY_TX_FEE = "min_relay_tx_fee"
    CLUSTER_LINEARIZATION = "cluster_linearization"
    MINIMUM_CHAIN_WORK = "minimum_chain_work"
    MEMPOOL_GRAPH = "mempool_graph"
    ALERT_NOTIFY = "alert_notify"
    PACKAGE_ACCEPTANCE = "package_acceptance"
    BLOCK_NOTIFY = "block_notify"
    SHUTDOWN_NOTIFY = "shutdown_notify"
    SETTINGS_FILE = "settings_file"
    PRECIOUS_BLOCK = "precious_block"
    SIGN_MESSAGE_WITH_PRIVKEY = "sign_message_with_privkey"
    ESTIMATE_SMART_FEE = "estimate_smart_fee"
    CHAIN_TIPS = "chain_tips"
    ASSUME_VALID = "assume_valid"
    INCREMENTAL_RELAY_FEE = "incremental_relay_fee"
    PEER_PERMISSIONS = "peer_permissions"
    CLUSTER_REPLACEMENT = "cluster_replacement"
    BLOCKS_DIR = "blocks_dir"
    INCLUDE_CONF = "include_conf"
    UTXO_SET_INFO = "utxo_set_info"
    MEMPOOL_ENTRY = "mempool_entry"
    ADDRESS_TABLES = "address_tables"


class SkipCounts:
    """One session's own tally of skips, one count per capability asked for.

    Session-scoped in `tests/integration/conftest.py`, so that one run
    against however many nodes prints one total per capability rather
    than one line per test -- the number rule 4 asks for is a count the
    node's tracker can read, not a running commentary.
    """

    def __init__(self) -> None:
        self._counts: Counter[Capability] = Counter()

    def record(self, capability: Capability) -> None:
        """Count one more skip against `capability`."""
        self._counts[capability] += 1

    def __iter__(self) -> Iterator[tuple[Capability, int]]:
        """Yield `(capability, count)` for every capability ever recorded.

        Sorted by the capability's own value, so the report below reads
        the same from one run to the next rather than in whatever order
        a `Counter` happens to have built up.
        """
        yield from sorted(self._counts.items(), key=lambda pair: pair[0].value)

    def report(self) -> str:
        """Return one line per capability recorded, or say that none was."""
        lines = [f"{capability.value}: {count}" for capability, count in self]
        if not lines:
            return "skips per capability: no test asked for one this run"
        return "skips per capability:\n" + "\n".join(lines)

    def as_mapping(self) -> dict[str, int]:
        """Return this tally as a plain `{capability.value: count}` mapping.

        `tests/integration/conftest.py` is what this is for: an xdist
        worker's own tally has to cross to the controller through
        `workeroutput`, and the channel that carries it serializes plain
        data, never an `Enum` member -- `add_mapping` is this method's
        own inverse, on the controller's own tally.
        """
        return {capability.value: count for capability, count in self}

    def add_mapping(self, mapping: Mapping[str, int]) -> None:
        """Add counts from another tally's own `as_mapping` into this one.

        :param mapping: a `{capability.value: count}` mapping, as
            `as_mapping` returns.
        """
        for value, count in mapping.items():
            self._counts[Capability(value)] += count


def require(
    capability: Capability, capabilities: AbstractSet[Capability], counts: SkipCounts
) -> None:
    """Raise `MissingCapabilityError` unless `capability` is in `capabilities`.

    :param capability: what the test about to run needs.
    :param capabilities: what the node under test declares, an adapter's
        own `capabilities`.
    :param counts: the session's own tally, credited before the raise so
        that a node lacking a capability still has that asked for it
        counted -- rule 4's "never a silent pass" reaching the count
        itself, not only the individual test.
    :raises MissingCapabilityError: where `capability` is missing.
    """
    if capability not in capabilities:
        counts.record(capability)
        msg = f"node does not declare {capability.value}"
        raise MissingCapabilityError(msg)
