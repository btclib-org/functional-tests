# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BitcoindAdapter`: the oracle, bitcoind on any chain it runs.

The release is the one `node-integration.yml` pins, fetched from
bitcoincore.org and verified against its published sha256; that
workflow's `core-master` job builds Core's `master` from source.
This module holds none of that fetch: it is handed the daemon's own path,
already installed -- `btclib-org/.github`'s `install_bitcoind.py` is what
installs it in CI, and `tests/integration/conftest.py` is what hands it
over.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, override

from bitcoin_core_rpc import BitcoinCoreRpcClient

from btclib_functional_tests.capability import Capability
from btclib_functional_tests.node import NodeAdapter
from btclib_functional_tests.timeout_factor import rpc_client_timeout

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet

__all__ = [
    "BitcoindAdapter",
]

# the wallet `mine` pays to, loaded from the datadir where a restart or
# an earlier adapter left it there, created where nothing did
_MINER_WALLET = "miner"

# the subdirectory of `-datadir` each chain writes its cookie and its
# `debug.log` into, the main chain writing into `-datadir` itself:
# `CreateBaseChainParams` (`src/chainparamsbase.cpp`), whose signet entry
# is `signet` for the default signet a command line with no
# `-signetchallenge` runs
_CHAIN_DIRS = {
    "main": "",
    "test": "testnet3",
    "testnet4": "testnet4",
    "signet": "signet",
    "regtest": "regtest",
}

# the capabilities a node on any chain but regtest lacks: `generatetoaddress`
# and `generateblock` give up after `maxtries` nonces (`src/rpc/mining.cpp`),
# a bound regtest's own target is met within and another chain's is not in
# practice;
# `setmocktime` refuses a chain that is not `IsMockableChain`
# (`src/rpc/node.cpp`), and so does `addconnection` (`src/rpc/net.cpp`);
# `-testactivationheight` is read by `ReadRegTestArgs`
# (`src/chainparams.cpp`) alone; `AppInitParameterInteraction`
# (`src/init.cpp`) refuses `-test` on any other chain; `_command`
# itself passes `-dnsseed=0` on any other chain, so `_check_extra_args`
# (`node.py`) refuses a test's own `-dnsseed` there, and `-connect=0`,
# under which `CConnman::Start` (`src/net.cpp`) never starts
# `ThreadOpenConnections`, the thread that asks a `-seednode`; and
# `mockscheduler` refuses a chain that is not `IsMockableChain`
# (`src/rpc/node.cpp`)
_REGTEST_ONLY = frozenset(
    {
        Capability.MINE,
        Capability.CLOCK,
        Capability.TEST_ACTIVATION_HEIGHT,
        Capability.TYPED_OUTBOUND,
        Capability.REINDEX_AFTER_FAILURE,
        Capability.GENERATE,
        Capability.DNS_SEED,
        Capability.ADDRESS_FETCH,
        Capability.PRIVATE_BROADCAST,
        Capability.ADDRESS_TABLES,
    }
)

# the capabilities a node on the main chain lacks: `-acceptnonstdtxn` is
# refused on a chain that is not `IsTestChain` (`src/node/mempool_args.cpp`)
_TEST_CHAIN_ONLY = frozenset({Capability.ACCEPT_NON_STANDARD})

# the capabilities a build without wallet support lacks, `_has_wallet`
# being what says so: `mine` pays a wallet's own address, and
# `NODE_WALLET` is the wallet itself
_WALLET_ONLY = frozenset({Capability.MINE, Capability.NODE_WALLET})

# the capabilities a build without the cluster mempool lacks,
# `_has_cluster_mempool` being what says so
_CLUSTER_MEMPOOL_ONLY = frozenset(
    {
        Capability.CLUSTER_LINEARIZATION,
        Capability.CLUSTER_REPLACEMENT,
        Capability.LIMIT_CLUSTER_COUNT,
        Capability.LIMIT_CLUSTER_SIZE,
    }
)

# the log category `_start` adds for a node given `-privatebroadcast`
_PRIVATE_BROADCAST_LOG = "-debug=privatebroadcast"


@lru_cache
def _has_wallet(executable: str) -> bool:
    """Return whether `executable` was built with wallet support.

    A Core build tree's own `test/config.ini` records this under
    `[components] ENABLE_WALLET` (`capability.py`'s own module docstring
    is the rule this reads), but that file sits beside a build tree's own
    binary -- `<build>/test/config.ini` beside `<build>/bin/bitcoind` --
    and nowhere near a release tarball's: the pinned release's tarball
    has no `test/` directory at all.
    `-help`'s own output is what every `bitcoind`, built or fetched,
    answers alike: `common/args.cpp`'s `ArgsManager::GetHelpMessage`
    prints a "Wallet options:" group only where some argument was
    registered under `OptionsCategory::WALLET`, which `wallet/init.cpp`'s
    `WalletInit::AddWalletOptions` does and `dummywallet.cpp`'s own
    `DummyWalletInit::AddWalletOptions` does not -- that implementation
    hides every wallet argument with `AddHiddenArgs` instead, so a build
    without wallet support still starts on `-disablewallet` but never
    mentions it in `-help` (measured against Core's `master`). A probe of
    the binary itself, in the same standing as `btclib_node.py`'s own
    `_writes_auth_cookie`: no port bound, no data directory created, and
    cached per executable because the answer is a fact about the build
    rather than about any one adapter instance.

    `ENABLE_WALLET` is the one `[components]` entry a capability
    `BitcoindAdapter` declares rests on
    ([ISS 53](https://github.com/btclib-org/functional-tests/issues/53)).
    `ENABLE_ZMQ`, `ENABLE_EXTERNAL_SIGNER`, `ENABLE_EMBEDDED_ASMAP` and
    `ENABLE_USDT_TRACEPOINTS` gate nothing a `Capability` member names --
    `-zmqpub*` and `getzmqnotifications`, `-signer` and
    `enumeratesigners`, a bare `-asmap` and `exportasmap`'s own answer,
    and tracepoints among what they do gate; each remaining entry of Core's
    `test/config.ini.in` names executables the build produced,
    `ENABLE_IPC`'s `bitcoin-node` among them, `bitcoind` itself never
    listening on `-ipcbind` (measured against Core's `master`). So for
    what this adapter declares, this probe answers everything the file
    would, and answers for a release too. A capability that comes to
    rest on a second entry is probed the same way, off the binary: the
    file would be a second source for the fact, and one a release cannot
    consult.

    `-nosettings` is not optional: `-help` alone still runs enough of
    `AppInit` to read and rewrite the *default* datadir's own
    `settings.json` -- unrelated to `-datadir`, which nothing here has
    passed yet -- and two probes racing on that file print "Settings
    file could not be written" in place of the help text, `Wallet
    options:` included, rather than refusing to start. Measured live
    against the release `node-integration.yml` pins, concurrent probes
    with `-nosettings` absent can answer `_has_wallet` `False` for a
    binary that has one -- `xdist`'s own several workers each
    constructing a `BitcoindAdapter` is exactly that concurrency.

    :param executable: the `bitcoind` binary to probe.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-help", "-nosettings"], check=False, capture_output=True
    )
    return b"\nWallet options:" in probe.stdout


@lru_cache
def _has_private_broadcast(executable: str) -> bool:
    """Return whether `executable` takes `-privatebroadcast`.

    `-help` lists the option from `v31.0` on; an older build lists none
    and refuses to start on it. Probed off the binary as `_has_wallet` is,
    with `-nosettings` for the same reason.

    :param executable: the `bitcoind` binary to probe.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-help", "-nosettings"], check=False, capture_output=True
    )
    return b"\n  -privatebroadcast\n" in probe.stdout


@lru_cache
def _has_proxy_per_network(executable: str) -> bool:
    """Return whether `executable` takes `-proxy=<proxy>=<network>`.

    The suffix arrives in `v30.0` (bitcoin/bitcoin#32425), and `-help`
    shows it as `[=<network>]` in the `-proxy` entry; an older build shows
    none and reads the `=` as part of the port. Probed off the binary as
    `_has_wallet` is, with `-nosettings` for the same reason.

    :param executable: the `bitcoind` binary to probe.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-help", "-nosettings"], check=False, capture_output=True
    )
    return b"[=<network>]\n" in probe.stdout


@lru_cache
def _has_cluster_mempool(executable: str) -> bool:
    """Return whether `executable` runs the cluster mempool.

    The cluster mempool arrives in `v31.0` (bitcoin/bitcoin#33629), with
    `-limitclustercount`, `-limitclustersize` and `getmempoolcluster`. The
    two options are debug-only, so `-help-debug` lists them and `-help`
    does not; an older build lists neither. Probed off the binary as
    `_has_wallet` is, with `-nosettings` for the same reason.

    :param executable: the `bitcoind` binary to probe.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-help-debug", "-nosettings"], check=False, capture_output=True
    )
    return b"\n  -limitclustercount=<n>\n" in probe.stdout


@lru_cache
def _has_reindex_after_failure(executable: str) -> bool:
    """Return whether `executable` takes `-test=reindex_after_failure_...`.

    The option, `reindex_after_failure_noninteractive_yes`, arrives in
    `v30.0` (bitcoin/bitcoin#32987). It is debug-only, so `-help-debug`
    lists it under `-test` and `-help` does not; an older build lists it
    nowhere. Probed off the binary as `_has_wallet` is, with `-nosettings`
    for the same reason.

    :param executable: the `bitcoind` binary to probe.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-help-debug", "-nosettings"], check=False, capture_output=True
    )
    return b"reindex_after_failure_noninteractive_yes" in probe.stdout


@lru_cache
def _has_deterministic_addrman(executable: str) -> bool:
    """Return whether `executable` takes `-test=addrman`.

    The option arrives in `v28.0` (bitcoin/bitcoin#29007);
    `-checkaddrman`, `getrawaddrman` and `getaddrmaninfo` are older. It is
    debug-only, so `-help-debug` lists it under `-test` and `-help` does
    not. Probed off the binary as `_has_wallet` is, with `-nosettings` for
    the same reason.

    :param executable: the `bitcoind` binary to probe.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-help-debug", "-nosettings"], check=False, capture_output=True
    )
    return b"addrman (use deterministic" in probe.stdout


class BitcoindAdapter(NodeAdapter):
    """A `bitcoind`: cookie authentication, and every capability.

    RPC authenticates by cookie, the file `-datadir` writes once the
    node is listening -- rule 1's "how RPC authenticates", answered by a
    file rather than a credential this adapter invents.

    `Capability.MINE` is `generatetoaddress` over a wallet this adapter
    loads or creates on use, and it is not a fact fixed for the whole
    class: `mine` needs a build with wallet support compiled in, which
    every release this repository fetches has, but a Core developer's own
    build tree can configure out
    (`cmake -DENABLE_WALLET=OFF`, or `--disable-wallet` under autotools)
    -- ISS 35's own rule (`capability.py`'s module docstring), a fact
    read from the running build rather than assumed for the whole class.
    `_has_wallet` above is the probe, and `__init__` below is what drops
    the capability from an instance built against a `bitcoind` lacking
    it, rather than declaring it and failing `mine`'s own wallet calls
    once a test actually calls it.
    `Capability.NODE_WALLET` is bitcoind's own built-in wallet, dropped by
    the same probe (`_WALLET_ONLY`): `_command` passes no
    `-disablewallet`, so a build with wallet support serves every wallet
    RPC. This adapter adds no method for it. A test creates the wallets
    it names and reaches each through `self.rpc.for_wallet(name)`,
    `bitcoin_core_rpc`'s own client for `/wallet/<name>`, which keeps this
    client's credential and transport, `--tracerpc` included. It names
    the wallet on every call, never the node's own endpoint: once `mine`
    has loaded `_MINER_WALLET` beside a test's own, bitcoind refuses a
    wallet method there that names no wallet (`RPC_WALLET_NOT_SPECIFIED`,
    `src/wallet/rpc/util.cpp`).
    `Capability.CONNECT` is `node.connect_nodes`
    (`node.py`), unconditional here since bitcoind answers `addnode` and
    `getnetworkinfo` the way every Core-compatible node does.
    `Capability.DISCONNECT` is `node.disconnect_nodes`'s own
    `disconnectnode`, and `Capability.BAN` is `setban`/`listbanned`/
    `clearbanned`: all three, like `addnode`, are in this release's own
    `help` listing with no argument, unconditional the same way (measured
    against the release `node-integration.yml` pins,
    [ISS functional-tests#43](https://github.com/btclib-org/functional-tests/issues/43)).
    `Capability.RAW_MESSAGE` is `sendmsgtopeer`, a debug RPC this release
    answers (measured against the same release: not in `help`'s own
    listing, which is for discoverability rather than availability, but
    `help sendmsgtopeer` answers its own signature) and which no other
    adapter's node offers yet. `Capability.BLK_FILES` is unconditional
    too: this is Core's own binary, so `blk*.dat` under `blocks/` is the
    format it already writes, not one this adapter has to add anything
    for. `Capability.DEBUG_LOG` is `debug_log_path` below, over the
    `-debug` categories `_command` enables: bitcoind's own binary is what
    writes Core's own wording, which is the fact this capability names.
    `Capability.UA_COMMENT` is unconditional too: `-uacomment` is Core's
    own flag, so this is the one node under test that always has it,
    whatever a later option capability turns out to name.
    `Capability.CLOCK` is `setmocktime`, wrapped by
    `NodeAdapter.set_mock_time` (`node.py`) -- a regtest-only RPC in the
    same standing as `sendmsgtopeer` above (measured against the same
    release: also absent from `help`'s own listing, and also answering
    `help setmocktime` directly). `Capability.RPC_AUTH_CONFIG` is
    unconditional too: `rpcauth`, `rpcwhitelist` and
    `rpcwhitelistdefault` are this binary's own `bitcoin.conf` keys,
    read the same way regardless of which adapter wrote the file.
    `Capability.RPC_AUTH_NEGATION` is unconditional too: `-norpcauth`
    disabling every `-rpcauth` given before it is `ArgsManager`'s own
    generic negation of a list-type setting, not a fact `-rpcauth` itself
    carries -- measured live against the release `node-integration.yml`
    pins, a request authenticated with a credential named on the command
    line before `-norpcauth` gets 401 once the node has started.
    `Capability.TEST_ACTIVATION_HEIGHT` is unconditional too:
    `-testactivationheight` is a debug-only flag of this binary's own,
    read by `ArgsManager` before any deployment is checked, so nothing
    about which deployment or which height is named changes whether the
    flag itself is recognised.
    `Capability.V2TRANSPORT` is unconditional too: `-v2transport` is this
    binary's own flag, defaulting to `1` (measured against `-help`'s own
    listing of the release `node-integration.yml` pins) -- node-to-node
    connections already report `transport_protocol_type: v2` in
    `getpeerinfo` with no argument at all, and `-v2transport=0` on either
    side falls back to `v1`. A `Peer` (`peer.py`) reaches this node over v1
    unless it is asked for v2, BIP324's own detection accepting a v1
    handshake from either side.
    `Capability.DATACARRIER`, `Capability.PERMIT_BARE_MULTISIG`,
    `Capability.DUST_RELAY_FEE`, `Capability.BYTES_PER_SIGOP` and
    `Capability.MAXMEMPOOL` are unconditional too: `-datacarrier`,
    `-datacarriersize`, `-permitbaremultisig`, `-dustrelayfee`,
    `-bytespersigop` and `-maxmempool` are all ordinary mempool/relay-policy
    flags of this binary's own, recognised regardless of what a caller sets
    them to
    ([ISS functional-tests#14](https://github.com/btclib-org/functional-tests/issues/14)).
    `Capability.DESCRIPTOR_ACTIVITY` and `Capability.BLOCK_STATS` are
    unconditional too: `getdescriptoractivity` and `getblockstats` are
    both RPCs of this binary's own, answered regardless of which
    descriptor, block or statistic a caller names.
    `Capability.FASTPRUNE`, `Capability.INBOUND_EVICTION` and
    `Capability.BLOCK_FILTER_INDEX` are unconditional too: `-fastprune`,
    `-maxconnections` and `-blockfilterindex` are this binary's own flags,
    and inbound eviction and `scanblocks` are its own behaviour behind
    the second and the third.
    `Capability.VALIDATE_ADDRESS` is unconditional too: `validateaddress`
    is this binary's own RPC (`src/rpc/output_script.cpp`), with no
    wallet behind it.
    `Capability.TYPED_OUTBOUND` is `addconnection`, wrapped by
    `NodeAdapter.add_outbound_connection` (`node.py`): a regtest-only RPC in
    the same standing as `setmocktime` above (measured against the release
    `node-integration.yml` pins: absent from `help`'s own listing, answering
    `help addconnection` directly).
    `Capability.RPC_WORK_QUEUE` is unconditional too: `-rpcthreads` and
    `-rpcworkqueue` are this binary's own flags, and the refusal of a
    request past the queue's bound is its own HTTP server's
    (`src/httpserver.cpp`).
    `Capability.BLOCKS_ONLY` and `Capability.BLOCK_FROM_PEER` are
    unconditional too: `-blocksonly` is this binary's own flag, and
    `getblockfrompeer` its own RPC (`src/rpc/blockchain.cpp`).
    `Capability.ACCEPT_NON_STANDARD` is `-acceptnonstdtxn`, this binary's
    own debug-only flag, which it refuses on main alone.
    `Capability.SUSPEND_NETWORK` is unconditional: `setnetworkactive`
    is this binary's own RPC (`src/rpc/net.cpp`).
    `Capability.DESCRIPTOR_INFO` is unconditional too: `getdescriptorinfo`
    is this binary's own RPC (`src/rpc/output_script.cpp`), with no
    wallet behind it.
    `Capability.PEER_TIMEOUT` and `Capability.MEMPOOL_EXPIRY` are
    unconditional too: `-peertimeout` and `-mempoolexpiry` are this
    binary's own flags (`src/init.cpp`).
    `Capability.SIGN_RAW_TRANSACTION` is unconditional:
    `signrawtransactionwithkey` and `combinerawtransaction` are this
    binary's own RPCs (`src/rpc/rawtransaction.cpp`), with no wallet
    behind them.
    `Capability.INVALIDATE_BLOCK` is unconditional too: `invalidateblock`
    and `reconsiderblock` are this binary's own RPCs
    (`src/rpc/blockchain.cpp`), and so is `scantxoutset`,
    `Capability.SCAN_UTXO_SET`'s.
    `Capability.GENERATE` is `generatetoaddress` and `generateblock`
    (`src/rpc/mining.cpp`), which need no wallet, unlike `mine`'s own
    `generatetoaddress` over one: it is declared on regtest alone, not
    narrowed by `_has_wallet`.
    `Capability.REINDEX_AFTER_FAILURE` is `-test`'s own
    `reindex_after_failure_noninteractive_yes`, a debug-only flag of this
    binary's own (`src/init.cpp`), which it refuses off regtest. It is
    declared only by a build whose `-help-debug` lists it,
    `_has_reindex_after_failure` being the probe.
    `Capability.PROXY`, `Capability.CJDNS`, `Capability.I2P_SAM` and
    `Capability.ONLYNET` are unconditional too: `-proxy`, `-onion`,
    `-proxyrandomize`, `-cjdnsreachable`, `-i2psam`, `-i2pacceptincoming`
    and `-onlynet` are this binary's own flags (`src/init.cpp`).
    `Capability.PROXY_PER_NETWORK` is `-proxy`'s `=<network>` suffix, this
    binary's own (`src/init.cpp`), declared only by a build whose `-help`
    shows it, `_has_proxy_per_network` being the probe.
    `Capability.TX_RECONCILIATION` and `Capability.PEER_BLOOM_FILTERS` are
    unconditional: `-txreconciliation` and `-peerbloomfilters` are
    this binary's own flags (`src/init.cpp`).
    `Capability.REINDEX` is unconditional: `-reindex` and
    `-reindex-chainstate` are this binary's own flags (`src/init.cpp`).
    `Capability.CAPTURE_MESSAGES` and `Capability.BLOCKS_XOR` are
    unconditional: `-capturemessages` and `-blocksxor` are this binary's
    own flags (`src/init.cpp`).
    `Capability.ORPHANAGE` is unconditional too: `getorphantxs` is this
    binary's own RPC (`src/rpc/mempool.cpp`), hidden from `help`'s own
    listing and answered on every chain.
    `Capability.BLOCK_PROPOSAL` is unconditional too: `getblocktemplate`
    is this binary's own RPC (`src/rpc/mining.cpp`), and its `proposal`
    mode answers ahead of the checks its `template` mode makes of the
    chain and the peers.
    `Capability.DNS_SEED` is `-dnsseed` and `-forcednsseed`, this binary's
    own flags (`src/init.cpp`), declared on regtest alone: on any other
    chain `_command` turns `-dnsseed` off itself.
    `Capability.ADDRESS_FETCH` is `-seednode`, this binary's own flag
    (`src/init.cpp`), declared on regtest alone: on any other chain
    `_command` passes `-connect=0`, under which no seed node is asked.
    `Capability.KNOWN_ADDRESSES` is unconditional: `addpeeraddress` and
    `getnodeaddresses` are this binary's own RPCs (`src/rpc/net.cpp`),
    the first hidden from `help`'s own listing.
    `Capability.EXTERNAL_IP` is unconditional too: `-externalip` is this
    binary's own flag (`src/init.cpp`).
    `Capability.PRIVATE_BROADCAST` is `-privatebroadcast`, this binary's
    own flag (`src/init.cpp`), with its `getprivatebroadcastinfo` and
    `abortprivatebroadcast` (`src/rpc/mempool.cpp`) and `mockscheduler`
    (`src/rpc/node.cpp`), declared on regtest alone, where
    `mockscheduler` answers, and only by a build whose `-help` lists the
    option, `_has_private_broadcast` being the probe.
    `Capability.STARTUP_NOTIFY` is unconditional too: `-startupnotify` is
    this binary's own flag (`src/init.cpp`).
    `Capability.DUMP_UTXO_SET` is unconditional too: `dumptxoutset` is
    this binary's own RPC (`src/rpc/blockchain.cpp`).
    `Capability.LOAD_BLOCK` is unconditional too: `-loadblock` is this
    binary's own flag (`src/init.cpp`).
    `Capability.LISTEN_ADDRESS` is unconditional too: `-port` and `-bind`
    are this binary's own flags (`src/init.cpp`). `_command` names a
    `-bind` of its own, so a test reaching the capability starts a
    subclass whose `_command` leaves that entry out.
    `Capability.MAX_TIP_AGE` is unconditional too: `-maxtipage` is this
    binary's own flag (`src/init.cpp`).
    `Capability.PEER_BLOCK_FILTERS` is unconditional too:
    `-peerblockfilters` is this binary's own flag (`src/init.cpp`), off
    unless a test's own arguments pass it.
    `Capability.RPC_INFO` is unconditional too: `getrpcinfo` is this
    binary's own RPC (`src/rpc/server.cpp`), its `logpath` naming
    `debug_log_path` below.
    `Capability.MIN_RELAY_TX_FEE` is unconditional too: `-minrelaytxfee`
    is this binary's own flag (`src/init.cpp`).
    `Capability.CLUSTER_LINEARIZATION`, `Capability.LIMIT_CLUSTER_COUNT`
    and `Capability.LIMIT_CLUSTER_SIZE` are the cluster mempool's:
    `getmempoolcluster` and `getmempoolfeeratediagram` are RPCs
    (`src/rpc/mempool.cpp`), `optimal` a field of `getmempoolinfo`, and
    `-limitclustercount` and `-limitclustersize` flags
    (`src/init.cpp`). `Capability.CLUSTER_REPLACEMENT` is too: the
    replacement limit counts clusters (`src/policy/rbf.cpp`). They are
    declared only by a build whose `-help-debug` lists
    `-limitclustercount`, `_has_cluster_mempool` being the probe.
    `Capability.MINIMUM_CHAIN_WORK` is unconditional too:
    `-minimumchainwork` is this binary's own debug-only flag
    (`src/init.cpp`).
    `Capability.MEMPOOL_GRAPH` is unconditional too:
    `getmempoolancestors`, `getmempooldescendants` and
    `gettxspendingprevout` are this binary's own RPCs
    (`src/rpc/mempool.cpp`), the last reading the mempool where no
    `-txospenderindex` is given.
    `Capability.ALERT_NOTIFY` is unconditional too: `-alertnotify` is
    this binary's own flag (`src/init.cpp`).
    `Capability.PACKAGE_ACCEPTANCE` is unconditional too: `submitpackage`
    and `testmempoolaccept`, each taking a package, are this binary's own
    RPCs (`src/rpc/mempool.cpp`), on every chain.
    `Capability.BLOCK_NOTIFY` and `Capability.SHUTDOWN_NOTIFY` are
    unconditional too: `-blocknotify` and `-shutdownnotify` are this
    binary's own flags (`src/init.cpp`).
    `Capability.SETTINGS_FILE` is unconditional too: `-settings` is this
    binary's own flag (`src/init.cpp`), its file read and written by
    `ArgsManager` (`src/common/args.cpp`).
    `Capability.PRECIOUS_BLOCK` is unconditional too: `preciousblock` is
    this binary's own RPC (`src/rpc/blockchain.cpp`).
    `Capability.SIGN_MESSAGE_WITH_PRIVKEY` is unconditional too:
    `signmessagewithprivkey` and `verifymessage` are this binary's own
    RPCs (`src/rpc/signmessage.cpp`), with no wallet behind them.
    `Capability.ESTIMATE_SMART_FEE` is unconditional too:
    `estimatesmartfee` and `estimaterawfee` are this binary's own RPCs
    (`src/rpc/fees.cpp`).
    `Capability.CHAIN_TIPS` is unconditional too: `getchaintips` is this
    binary's own RPC (`src/rpc/blockchain.cpp`).
    `Capability.ASSUME_VALID` is unconditional too: `-assumevalid` is this
    binary's own flag (`src/init.cpp`).
    `Capability.INCREMENTAL_RELAY_FEE` is unconditional too:
    `-incrementalrelayfee` is this binary's own flag
    (`src/node/mempool_args.cpp`).
    `Capability.PEER_PERMISSIONS` is unconditional too: `-whitelist` and
    `-whitebind` are this binary's own flags (`src/init.cpp`).
    `Capability.BLOCKS_DIR` is unconditional too: `-blocksdir` is this
    binary's own flag (`src/init.cpp`).
    `Capability.INCLUDE_CONF` is unconditional too: `includeconf` is a key
    of this binary's own configuration file and `-includeconf` a flag it
    refuses on the command line (`src/common/args.cpp`).
    `Capability.UTXO_SET_INFO` and `Capability.MEMPOOL_ENTRY` are
    unconditional too: `gettxoutsetinfo` and `getmempoolentry` are this
    binary's own RPCs (`src/rpc/blockchain.cpp`, `src/rpc/mempool.cpp`).
    `Capability.ADDRESS_TABLES` is `getrawaddrman` and `getaddrmaninfo`,
    this binary's own RPCs (`src/rpc/net.cpp`), with `-test`'s own
    `addrman` and `-checkaddrman`, its debug-only flags (`src/init.cpp`).
    It is declared on regtest alone, where `-test` is taken, and only by
    a build whose `-help-debug` lists `addrman` under `-test`,
    `_has_deterministic_addrman` being the probe.

    Every chain the release runs is in `chains`. On any chain but regtest
    an instance drops `_REGTEST_ONLY`'s capabilities, which only regtest
    answers or which `_command` turns off elsewhere, and on main
    `_TEST_CHAIN_ONLY`'s too.
    """

    capabilities: AbstractSet[Capability] = frozenset(
        {
            Capability.MINE,
            Capability.CONNECT,
            Capability.DISCONNECT,
            Capability.BAN,
            Capability.RAW_MESSAGE,
            Capability.BLK_FILES,
            Capability.DEBUG_LOG,
            Capability.UA_COMMENT,
            Capability.CLOCK,
            Capability.RPC_AUTH_CONFIG,
            Capability.RPC_AUTH_NEGATION,
            Capability.TEST_ACTIVATION_HEIGHT,
            Capability.V2TRANSPORT,
            Capability.DATACARRIER,
            Capability.PERMIT_BARE_MULTISIG,
            Capability.DUST_RELAY_FEE,
            Capability.BYTES_PER_SIGOP,
            Capability.LIMIT_CLUSTER_COUNT,
            Capability.LIMIT_CLUSTER_SIZE,
            Capability.MAXMEMPOOL,
            Capability.DESCRIPTOR_ACTIVITY,
            Capability.BLOCK_STATS,
            Capability.FASTPRUNE,
            Capability.INBOUND_EVICTION,
            Capability.BLOCK_FILTER_INDEX,
            Capability.VALIDATE_ADDRESS,
            Capability.TYPED_OUTBOUND,
            Capability.RPC_WORK_QUEUE,
            Capability.BLOCKS_ONLY,
            Capability.BLOCK_FROM_PEER,
            Capability.ACCEPT_NON_STANDARD,
            Capability.SUSPEND_NETWORK,
            Capability.DESCRIPTOR_INFO,
            Capability.PEER_TIMEOUT,
            Capability.MEMPOOL_EXPIRY,
            Capability.SIGN_RAW_TRANSACTION,
            Capability.INVALIDATE_BLOCK,
            Capability.REINDEX_AFTER_FAILURE,
            Capability.GENERATE,
            Capability.SCAN_UTXO_SET,
            Capability.PROXY,
            Capability.PROXY_PER_NETWORK,
            Capability.CJDNS,
            Capability.I2P_SAM,
            Capability.ONLYNET,
            Capability.NODE_WALLET,
            Capability.TX_RECONCILIATION,
            Capability.PEER_BLOOM_FILTERS,
            Capability.REINDEX,
            Capability.CAPTURE_MESSAGES,
            Capability.BLOCKS_XOR,
            Capability.ORPHANAGE,
            Capability.BLOCK_PROPOSAL,
            Capability.DNS_SEED,
            Capability.ADDRESS_FETCH,
            Capability.KNOWN_ADDRESSES,
            Capability.EXTERNAL_IP,
            Capability.PRIVATE_BROADCAST,
            Capability.STARTUP_NOTIFY,
            Capability.DUMP_UTXO_SET,
            Capability.LOAD_BLOCK,
            Capability.LISTEN_ADDRESS,
            Capability.MAX_TIP_AGE,
            Capability.PEER_BLOCK_FILTERS,
            Capability.RPC_INFO,
            Capability.MIN_RELAY_TX_FEE,
            Capability.CLUSTER_LINEARIZATION,
            Capability.MINIMUM_CHAIN_WORK,
            Capability.MEMPOOL_GRAPH,
            Capability.ALERT_NOTIFY,
            Capability.PACKAGE_ACCEPTANCE,
            Capability.BLOCK_NOTIFY,
            Capability.SHUTDOWN_NOTIFY,
            Capability.SETTINGS_FILE,
            Capability.PRECIOUS_BLOCK,
            Capability.SIGN_MESSAGE_WITH_PRIVKEY,
            Capability.ESTIMATE_SMART_FEE,
            Capability.CHAIN_TIPS,
            Capability.ASSUME_VALID,
            Capability.INCREMENTAL_RELAY_FEE,
            Capability.PEER_PERMISSIONS,
            Capability.CLUSTER_REPLACEMENT,
            Capability.BLOCKS_DIR,
            Capability.INCLUDE_CONF,
            Capability.UTXO_SET_INFO,
            Capability.MEMPOOL_ENTRY,
            Capability.ADDRESS_TABLES,
        }
    )
    chains: AbstractSet[str] = frozenset(_CHAIN_DIRS)

    def __init__(
        self,
        executable: str,
        datadir: Path,
        rpc_port: int,
        p2p_port: int,
        extra_args: Sequence[str] = (),
        rpc_auth: tuple[str, str] | None = None,
        *,
        trace_rpc: bool = False,
        chain: str = "regtest",
    ) -> None:
        """Construct the adapter, then drop what the build or the chain lacks.

        `super().__init__` runs first, the same ordering
        `BtclibNodeAdapter.__init__` (`btclib_node.py`) uses and for the
        same reason: `_check_extra_args(self._command(), extra_args)`
        needs `self._executable` set before `_command` can be called.
        `_has_wallet`, `_has_private_broadcast`, `_has_cluster_mempool`,
        `_has_reindex_after_failure`, `_has_proxy_per_network` and
        `_has_deterministic_addrman` are then this class's own per-build
        probes, read once per instance rather than once per `mine` call,
        `_REGTEST_ONLY` is what any other `chain` drops, and
        `_TEST_CHAIN_ONLY` what main drops besides.
        """
        super().__init__(
            executable,
            datadir,
            rpc_port,
            p2p_port,
            extra_args,
            rpc_auth,
            trace_rpc=trace_rpc,
            chain=chain,
        )
        if not _has_wallet(executable):
            self.capabilities = self.capabilities - _WALLET_ONLY
        if not _has_private_broadcast(executable):
            self.capabilities = self.capabilities - {Capability.PRIVATE_BROADCAST}
        if not _has_cluster_mempool(executable):
            self.capabilities = self.capabilities - _CLUSTER_MEMPOOL_ONLY
        if not _has_reindex_after_failure(executable):
            self.capabilities = self.capabilities - {Capability.REINDEX_AFTER_FAILURE}
        if not _has_proxy_per_network(executable):
            self.capabilities = self.capabilities - {Capability.PROXY_PER_NETWORK}
        if not _has_deterministic_addrman(executable):
            self.capabilities = self.capabilities - {Capability.ADDRESS_TABLES}
        if chain != "regtest":
            self.capabilities = self.capabilities - _REGTEST_ONLY
        if chain == "main":
            self.capabilities = self.capabilities - _TEST_CHAIN_ONLY

    @override
    def _command(self) -> list[str]:
        """Return bitcoind's own argv, a loopback-only, ephemeral node.

        `-natpmp=0`, `-discover=0` and `-listenonion=0`: `-bind` already
        names the one address this node listens on regardless of
        `-listen`'s own default, so nothing here needs to reach a
        gateway, a public address lookup or a Tor control port -- a
        throwaway node run from a test suite wants none of the
        three. `-fallbackfee` is set because a chain with no fee history
        refuses to fund a transaction without it, which `Capability.MINE`
        meets the moment a caller spends what it mines. The `-debug`
        entries are `Capability.DEBUG_LOG`'s own condition: those of the
        log lines the log family's tests read that are `LogDebug`'s
        (`src/util/log.h`) are each in a category of Core's own, and print
        at all only where that category is enabled. `-debug` accumulates,
        each occurrence enabling one more category, so each is an entry of
        its own rather than one replacing another --
        unconditional here rather than left to a per-test option, since
        `_check_extra_args` (`node.py`) refuses an `extra_args` entry
        naming `-debug` once this argv sets it. `privatebroadcast` is the
        one category left to `_start`, which adds it where `-privatebroadcast`
        is asked for.

        `-unsafesqlitesync` is Core's own `write_config` (`util.py`) line,
        written there "so that the tests don't timeout": it turns a wallet's
        SQLite `synchronous` pragma off (`src/wallet/sqlite.cpp`), so a
        block a loaded wallet connects waits on no flush to disk. Without
        it, one `generatetoaddress` over a loaded wallet waits on those
        flushes, and mines blocks much more slowly.
        [ISS 226](https://github.com/btclib-org/functional-tests/issues/226)
        has such a call outlasting the RPC client's own timeout on a loaded
        machine. A build without wallet support starts on it too:
        `DummyWalletInit::AddWalletOptions` (`src/dummywallet.cpp`) hides it
        rather than leaving it unknown.

        `-rpcservertimeout=99000` is Core's own `write_config` (`util.py`)
        line too, written there to "disable server-side timeouts to avoid
        intermittent issues". bitcoind closes an RPC connection idle for
        `-rpcservertimeout` seconds, 30 by default
        (`DEFAULT_HTTP_SERVER_TIMEOUT`, `src/httpserver.h`,
        bitcoin/bitcoin@f0de87f040; measured against the release
        `node-integration.yml` pins, a kept connection still open after 25
        seconds idle and closed by 35), while `NodeAdapter` keeps its
        connections open across calls (`_ThreadSessions`, `node.py`).
        `SessionTransport` probes a kept connection before reusing it and
        does not retry a bare `ConnectionResetError` its read raises, so a
        close landing between that probe and the request can reach the
        caller as a `FetchError` raised from one
        ([ISS 336](https://github.com/btclib-org/functional-tests/issues/336)).

        `-rpcallowip=127.0.0.1` is what makes `-rpcbind` bind at all:
        Core's `GetBindAddresses` (`src/httpserver.cpp`,
        bitcoin/bitcoin@f0de87f040) ignores `-rpcbind` unless
        `-rpcallowip` is also given and binds `::1` and `127.0.0.1`
        instead, and `InitHTTPServer` starts where either one binds. With
        both given, `127.0.0.1` is the one RPC endpoint, so a port
        another process already holds there fails init and the process
        exits, which `_wait_for_rpc` (`node.py`) reports on its exit code
        rather than waiting out its timeout against a node answering on
        `::1` alone (measured against the release `node-integration.yml`
        pins,
        [ISS 135](https://github.com/btclib-org/functional-tests/issues/135)).
        It admits no client the default does not: `InitHTTPAllowList`
        admits `127.0.0.0/8` and `::1` whatever `-rpcallowip` names.
        Core's own framework passes neither option (`util.py`'s
        `write_config`), its ports coming from `--portseed` (`util.py`'s
        `p2p_port`) unprobed, where `free_ports` (`node.py`) binds each
        once before handing it out. `-bind` needs no such partner:
        `CConnman::InitBinds` (`src/net.cpp`) fails init on any `-bind`
        address it cannot bind.

        `-chain` names the chain. On any chain but regtest, `-connect=0`,
        `-dnsseed=0` and `-fixedseeds=0` keep the node off the real
        network: no outbound connection is drawn, and no DNS or fixed seed
        is asked for one. Regtest's seed, `dummySeed.invalid.`, resolves
        nowhere, so a regtest node reaches it only through a proxy, and a
        test giving the node one passes `-dnsseed=0` itself
        [ISS 205](https://github.com/btclib-org/functional-tests/issues/205).
        Core's own `write_config` (`util.py`) writes all three on every chain,
        `connect=0` unless a test passes `disable_autoconnect=False`.
        `-connect=0` would turn listening off by default, but
        `InitParameterInteraction` (`src/init.cpp`) reads `-bind` first
        and keeps listening on the address it names.
        """
        isolation = (
            []
            if self._chain == "regtest"
            else ["-connect=0", "-dnsseed=0", "-fixedseeds=0"]
        )
        return [
            self._executable,
            f"-chain={self._chain}",
            f"-datadir={self._datadir}",
            f"-rpcport={self._rpc_port}",
            "-rpcbind=127.0.0.1",
            "-rpcallowip=127.0.0.1",
            f"-bind=127.0.0.1:{self._p2p_port}",
            "-natpmp=0",
            "-discover=0",
            "-listenonion=0",
            "-fallbackfee=0.0002",
            "-printtoconsole=0",
            "-unsafesqlitesync",
            "-rpcservertimeout=99000",
            "-debug=net",
            "-debug=addrman",
            "-debug=txreconciliation",
            "-debug=reindex",
            "-debug=validation",
            "-debug=i2p",
            "-debug=mempoolrej",
            "-debug=http",
            *isolation,
        ]

    @override
    def _start(self, extra_args: tuple[str, ...]) -> None:
        """Start as `NodeAdapter._start` does, adding the private broadcast log.

        `-debug=privatebroadcast` is appended where an entry of `extra_args`
        names `-privatebroadcast`, and only there: a build refuses to start
        on a `-debug` category it does not know (`Unsupported logging
        category`), and this one is a category from `v31.0` on, so every
        older bitcoind would refuse to start on an unconditional one. The
        lines a test reads under it are those of a node given
        `-privatebroadcast`, the category logging from private broadcast's
        own code.

        :param extra_args: already checked against `_command`, by
            `__init__` or by `restart`.
        """
        if any(
            arg.lstrip("-").split("=", 1)[0] == "privatebroadcast" for arg in extra_args
        ):
            extra_args = (*extra_args, _PRIVATE_BROADCAST_LOG)
        super()._start(extra_args)

    @property
    def _chain_dir(self) -> Path:
        """Return the directory of this node's cookie and log: `_CHAIN_DIRS`."""
        return self._datadir / _CHAIN_DIRS[self._chain]

    @override
    def _rpc_client(self) -> BitcoinCoreRpcClient:
        """Return a client authenticating the way this node was configured.

        The cookie `-datadir` writes, unless `rpc_auth` (`NodeAdapter.__init__`)
        names a credential instead: a node started with `-rpcuser`/
        `-rpcpassword` or `-norpccookiefile` (`extra_args`) writes no
        cookie at all, so nothing here can wait on a file that never
        appears -- the caller that put either flag on the command line is
        the one that already knows the credential to authenticate with.
        Either client is built over `NodeAdapter._rpc_transport`, this
        adapter's own connections, `--tracerpc` included. Either bounds each
        call by
        `timeout_factor.rpc_client_timeout`, read when the client is built,
        so `--timeout-factor` scales it as Core's own harness scales its RPC
        connection's.
        """
        url = f"http://127.0.0.1:{self._rpc_port}"
        transport = self._rpc_transport()
        timeout = rpc_client_timeout()
        if self._rpc_auth is not None:
            user, password = self._rpc_auth
            return BitcoinCoreRpcClient(
                url,
                user=user,
                password=password,
                timeout=timeout,
                transport=transport,
            )
        return BitcoinCoreRpcClient(
            url,
            cookie_path=self._chain_dir / ".cookie",
            timeout=timeout,
            transport=transport,
        )

    @property
    def debug_log_path(self) -> Path:
        """Return this node's own `debug.log`, `Capability.DEBUG_LOG`'s fact.

        `debug.log` in `_chain_dir`, the directory the cookie file above
        is read from -- bitcoind's own convention, not a name this adapter
        invents.
        """
        return self._chain_dir / "debug.log"

    @override
    def _log_path(self) -> Path:
        """Return `debug_log_path`, for `start`'s own `TimeoutError`."""
        return self.debug_log_path

    def _load_miner_wallet(self) -> None:
        """Make `_MINER_WALLET` loaded: already, loaded from disk, or created.

        Asked of the node on every call rather than cached per adapter: a
        restart leaves the wallet on disk and unloaded, and a second adapter
        over the same datadir finds the first one's there, where
        `createwallet` answers "Database already exists" (measured against
        the release `node-integration.yml` pins,
        [ISS 86](https://github.com/btclib-org/functional-tests/issues/86)).
        `listwalletdir` is what names a wallet on disk and unloaded.
        """
        loaded = self.rpc.call("listwallets")
        if isinstance(loaded, list) and _MINER_WALLET in loaded:
            return
        walletdir = self.rpc.call("listwalletdir")
        on_disk = walletdir.get("wallets", []) if isinstance(walletdir, dict) else []
        names = {entry.get("name") for entry in on_disk if isinstance(entry, dict)}
        method = "loadwallet" if _MINER_WALLET in names else "createwallet"
        self.rpc.call(method, [_MINER_WALLET])

    @override
    def mine(self, count: int = 1) -> list[str]:
        """Mine `count` blocks to this adapter's wallet, return their hashes.

        `generatetoaddress`, over the wallet `_load_miner_wallet` makes
        loaded first: Core's own regtest mining needs an address to pay,
        and a wallet answers `getnewaddress` with one that this same
        client can later spend from, which is `Capability.MINE`'s whole
        promise rather than only a taller chain.

        :param count: how many blocks to mine.
        :returns: the mined blocks' own hashes, Core's own
            `generatetoaddress` return value.
        """
        self._load_miner_wallet()
        wallet_rpc = self.rpc.for_wallet(_MINER_WALLET)
        address = wallet_rpc.call("getnewaddress")
        hashes = self.rpc.call("generatetoaddress", [count, address])
        if not isinstance(hashes, list):
            err_msg = f"generatetoaddress answered {hashes!r}, not a list of hashes"
            raise TypeError(err_msg)
        return hashes
