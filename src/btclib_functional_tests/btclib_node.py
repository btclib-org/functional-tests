# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BtclibNodeAdapter`: the first target, `btclib-node` over `python -m`.

[btclib-node PR 1012](https://github.com/btclib-org/btclib-node/pull/1012)
is what this adapter needs already served: `getblock`, `submitblock`,
`addnode` and `getnetworkinfo`, measured present in
`src/btclib_node/rpc/callbacks.py`'s own dispatch table before this
module was written.

The measurements below are as of the PyPI release `2026.9.24`, unless a
paragraph names another build.

`Capability.MINE` is declared per instance, by `_connects_alone`'s
own probe. `mine` (`NodeAdapter.mine`) builds and solves each block
client-side and hands it to `submitblock`, and a node with no peer never leaves
`NodeStatus.SyncingHeaders`, so what decides is whether the build's own
`main.update_chain` connects a block at that status: `main` from
btclib-node PR 1152 (`84277406`) on, the fix
[ISS btclib-node#1071](https://github.com/btclib-org/btclib-node/issues/1071)
asked for. Measured at `main` (`35b26d2e`): a solo node accepts the block
and `getblockcount`/`getbestblockhash` move onto it. The released
`2026.9.24` (`422d2640`) answers the same `submitblock` `None` (accepted)
and leaves both at genesis, so an instance built against it does not gain
the capability.

At the released `2026.9.24`, `block_db` never holds the genesis block, so
neither `getblock` nor a p2p `getdata` can serve the one block a fresh regtest
node -- mined or not -- starts at
([ISS btclib-node#1072](https://github.com/btclib-org/btclib-node/issues/1072)).

`Capability.BLK_FILES` is not declared either, and is not a gap this
adapter is waiting on: `block_db.BlockDB` is its own on-disk format, not
Core's `blk*.dat`, by the decision
[ISS btclib-node#573](https://github.com/btclib-org/btclib-node/issues/573)
already made and closed on -- reading Core's own files was refused in
favour of `-connect`/`-addnode` delivering the same blocks over loopback
p2p, which this repository's own `Capability.CONNECT` already reaches.

`Capability.DISCONNECT` is declared per instance, by `_serves_disconnect`'s
own probe: a build whose `rpc/callbacks.py` names `disconnectnode` in its
public `callbacks`, as `main` (`053d5d83`) does from `24de126d` on
([ISS btclib-node#1193](https://github.com/btclib-org/btclib-node/issues/1193)).
The released `2026.9.24` (`422d2640`) names no such callback, so an instance
built against it does not gain the capability.

`Capability.BAN` is declared per instance, by `_serves_ban_list`'s own
probe: a build whose `rpc/callbacks.py` names `setban`, `listbanned` and
`clearbanned` in its public `callbacks`, the table `rpc/main.py` resolves
every request's method through -- `main` from btclib-node PR 1275
(`65510d56`) on, the ban list
[ISS btclib-node#1088](https://github.com/btclib-org/btclib-node/issues/1088)
asked for. The released `2026.9.24` (`422d2640`) names none of the three,
so an instance built against it does not gain the capability.

`Capability.UA_COMMENT` is not declared: measured against `cli.py`'s own
`_build_parser` at the released `2026.9.24` (`422d2640`) and its `_OPTIONS`
at `main` (`8ded5494`) alike, `-uacomment` is registered by neither.

`Capability.CLOCK` is declared per instance, by `_serves_setmocktime`'s
own probe: a build whose `rpc/callbacks.py` names `setmocktime` in its
public `callbacks` -- from btclib-node PR 1927 (`a8263aa`) on, the RPC
[ISS btclib-node#1479](https://github.com/btclib-org/btclib-node/issues/1479)
asked for. `Capability.GENERATE` is declared per instance, by
`_serves_generate`'s own probe: a build whose `rpc/callbacks.py` names
`generatetoaddress` and `generateblock` -- from btclib-node PR 1643
(`57fed26`) on, the RPCs
[ISS btclib-node#1404](https://github.com/btclib-org/btclib-node/issues/1404)
and [ISS btclib-node#1396](https://github.com/btclib-org/btclib-node/issues/1396)
asked for. Both are measured at the released `2026.10.30` (`30e9573`)
and at `main` (`995a2b46`). The released `2026.9.24` (`422d2640`) names
none of the three, so an instance built against it gains neither
capability. Both are regtest's alone, as `_REGTEST_ONLY` below says.

`Capability.RPC_AUTH_CONFIG` is not a class-level fact the way
`BLK_FILES` and `UA_COMMENT` above are, and unlike
them this is not a fact about `btclib-node` itself: `cli.py`'s own
`_RECOGNIZED_KEYS` at `18b6ae1e2c74` already names `rpcauth`,
`rpcwhitelist` and `rpcwhitelistdefault`, landed by
[ISS btclib-node#1070](https://github.com/btclib-org/btclib-node/issues/1070)
alongside the cookie authentication `_writes_auth_cookie` above already
probes for -- so it is a fact about which build the executable an
instance is constructed with names, and `__init__` below declares it on
that instance by riding on the same probe rather than by a second one:
a build whose `btclib_node.rpc.auth` imports (`_writes_auth_cookie`
returns `True`) also recognises those three keys, both having landed in
the same commit. The class-level `capabilities` holds the facts true of
every build, `_EVERY_BUILD`; an instance built with an executable
carrying `rpc.auth` gains `Capability.RPC_AUTH_CONFIG` on top of it. A
build predating that issue warns `ignoring unknown configuration value
rpcauth` and starts anyway rather than enforcing it (measured live at
the released `2026.9.24`), so an instance built against it does not gain
the capability; one built against a build carrying #1070 does.

`Capability.RPC_AUTH_NEGATION` is declared per instance too, by
`_negates_rpcauth`'s own probe: a build whose `cli.py` reads `-noname` as
`-name` negated, `rpcauth` among its `_OPTIONS`, discards every
`-rpcauth` given before `-norpcauth` -- `main` from btclib-node PR 1165
(`995dd1d0`) on, the behaviour
[ISS btclib-node#1176](https://github.com/btclib-org/btclib-node/issues/1176)
asks for. `_writes_auth_cookie` does not answer it: `995dd1d0`'s parent
`b1184d7c` imports `btclib_node.rpc.auth` and still refuses `-norpcauth`
as an "Invalid parameter". The released `2026.9.24` (`422d2640`) refuses
it as argparse's "unrecognized arguments", `-nolisten` being the one
negated spelling its `_build_parser` registers, so an instance built
against it does not gain the capability.

`Capability.V2TRANSPORT` is declared per instance too, by
`_speaks_v2`'s own probe: a build whose `cli.build_config` reads
`-nov2transport` as `v2transport` off -- `main` from btclib-node PR 1675
(`3f7d2b19`) on, step D2 of
[ISS btclib-node#1190](https://github.com/btclib-org/btclib-node/issues/1190)
-- has the flag, the BIP324 codec behind it, and `getpeerinfo`'s own
`transport_protocol_type` and `session_id`. The released `2026.9.24`
(`422d2640`) refuses the argument as argparse's "unrecognized
arguments", so an instance built against it does not gain the capability.

`BtclibNodeAdapter._command` adds `-v1transport=1` where
`_accepts_v1transport`'s probe holds: a `Peer` speaks v1 unless asked
for v2, and btclib-node refuses v1 under `-v1transport=0`
([ISS btclib-node#1190](https://github.com/btclib-org/btclib-node/issues/1190)).
A build without the flag, such as the PyPI `2026.9.24`, gets no
`-v1transport`.

`Capability.TEST_ACTIVATION_HEIGHT` is declared per instance too, by
`_moves_activation_heights`'s own probe: a build whose `cli.build_config`
reads `-testactivationheight=segwit@5` into its own chain's
`segwit_height` -- the released `2026.11.2` (`a51c9490`), and `main` from
btclib-node PR 2173 (`cb346057`) on, the option
[ISS btclib-node#2155](https://github.com/btclib-org/btclib-node/issues/2155)
asked for. The released `2026.10.30` (`30e95735`) refuses the argument as
an "Invalid parameter", so an instance built against it does not gain the
capability. A build with the option refuses it on mainnet, so an instance
on chain `main` does not gain the capability either.

`Capability.INBOUND_EVICTION` is declared per instance too, by
`_evicts_inbound`'s own probe: a build carrying
`btclib_node.p2p.eviction`, a port of Core's `SelectNodeToEvict`
landed by
[ISS btclib-node#1064](https://github.com/btclib-org/btclib-node/issues/1064),
disconnects an unprotected inbound peer once its inbound slots are full,
and registers `-maxconnections` to bound them. PyPI's `2026.9.24`
release carries neither, so an instance built against it does not gain
the capability.

`Capability.DESCRIPTOR_ACTIVITY` and `Capability.BLOCK_STATS` are never
declared, on either build: neither `getdescriptoractivity` nor
`getblockstats` names a callback in `src/btclib_node/rpc/callbacks.py`'s
own dispatch table, measured at the released `2026.9.24` (`422d2640`)
and at `main` (`d7693b2f5a16`) alike.

`Capability.VALIDATE_ADDRESS` is never declared either: `validateaddress`
names no callback in that same dispatch table, measured at the released
`2026.9.24` (`422d2640`) and at `main` (`d0ead5f0`) alike.

`Capability.RPC_WORK_QUEUE` is never declared either: `cli.py` registers
neither `-rpcthreads` nor `-rpcworkqueue`, measured at the released
`2026.9.24` and at `main` (`25776772`) alike.

`Capability.BLOCKS_ONLY` and `Capability.BLOCK_FROM_PEER` are never
declared either: `cli.py` registers no `-blocksonly`, and
`getblockfrompeer` names no callback in that same dispatch table,
measured at the released `2026.9.24` and at `main` (`25776772`) alike.

`Capability.ACCEPT_NON_STANDARD` is never declared either:
`-acceptnonstdtxn` is not among the flags `-help -noconf` prints, measured
at the released `2026.9.24` and at `main` (`19c5661e`) alike.

`Capability.DESCRIPTOR_INFO` is never declared either: `getdescriptorinfo`
names no callback in `src/btclib_node/rpc/callbacks.py`'s own dispatch
table, measured at the released `2026.9.24` (`422d2640`) and at `main`
(`4e155386`) alike.

`Capability.PEER_TIMEOUT` and `Capability.MEMPOOL_EXPIRY` are never
declared either: `cli.py` registers neither `-peertimeout` nor
`-mempoolexpiry`, measured at the released `2026.9.24` (`422d2640`) and
at `main` (`4e155386`) alike.

`Capability.REINDEX_AFTER_FAILURE` is never declared either: `cli.py`
registers no `-test`, measured at the released `2026.9.24` and at `main`
(`4e155386`) alike, each refusing
`-test=reindex_after_failure_noninteractive_yes` as an unknown argument.

`Capability.PROXY` is never declared either: `cli.py` registers none of
`-proxy`, `-onion` and `-proxyrandomize`, measured against its
`_build_parser` at the released `2026.9.24` (`422d2640`) and its
`_OPTIONS` at `main` (`d2b4efa5`) alike. Nor is
`Capability.PROXY_PER_NETWORK`, the suffix of an option it does not
register. Nor are `Capability.CJDNS`, `Capability.I2P_SAM` and
`Capability.ONLYNET`: it registers none of `-cjdnsreachable`, `-i2psam`,
`-i2pacceptincoming` and `-onlynet`, measured the same way at the
released `2026.9.24` and at `main`
(`1aeebc67`).

`Capability.NODE_WALLET` is never declared either: `btclib-node` keeps
no wallet, `src/btclib_node/rpc/callbacks.py`'s own dispatch table naming
no wallet RPC -- no `createwallet`, `getnewaddress` or `signmessage` --
measured at the released `2026.9.24` and at `main` (`d2b4efa5`) alike. A
wallet kept beside the node is `wallet.WalletAdapter`'s to reach.

`Capability.TX_RECONCILIATION` and `Capability.PEER_BLOOM_FILTERS` are
never declared either: `cli.py` registers neither `-txreconciliation` nor
`-peerbloomfilters`, measured at the released `2026.9.24` (`422d2640`)
and at `main` (`dca9c2be`) alike, `txreconciliation` being a `-debug`
category at `main`.

`Capability.REINDEX` is never declared either: each build refuses
`-reindex` and `-reindex-chainstate` as unknown arguments, measured at the
released `2026.9.24` and at `main` (`338f64c5`) alike
([ISS btclib-node#1415](https://github.com/btclib-org/btclib-node/issues/1415)).

`Capability.CAPTURE_MESSAGES` and `Capability.BLOCKS_XOR` are never
declared either: `cli.py` registers neither `-capturemessages` nor
`-blocksxor`, measured against its `_build_parser` at the released
`2026.9.24` (`422d2640`) and its `_OPTIONS` at `main` (`98448c4d`) alike.

`Capability.ORPHANAGE` is declared per instance, by
`_serves_getorphantxs`'s own probe: a build whose `rpc/callbacks.py` names
`getorphantxs` in its dispatch table. The released `2026.10.4` does not
and `main` (`ab2a63cc`) does.

`Capability.ADDRESS_FETCH` is never declared either: `cli.py` registers
no `-seednode`, measured against its `_build_parser` at the released
`2026.9.24` (`422d2640`) and its `_OPTIONS` at `main` (`d4559960`) alike
([ISS btclib-node#1192](https://github.com/btclib-org/btclib-node/issues/1192)).

`Capability.DNS_SEED` is never declared either: `cli.py` registers
neither `-dnsseed` nor `-forcednsseed`, measured against its
`_build_parser` at the released `2026.9.24` (`422d2640`) and its
`_OPTIONS` at `main` (`d4559960`) alike
([ISS btclib-node#1192](https://github.com/btclib-org/btclib-node/issues/1192),
[ISS btclib-node#1265](https://github.com/btclib-org/btclib-node/issues/1265)).

`Capability.EXTERNAL_IP` is never declared either: `cli.py` registers no
`-externalip`, measured at the released `2026.9.24` (`422d2640`) and at
`main` (`d4559960`) alike
([ISS btclib-node#1445](https://github.com/btclib-org/btclib-node/issues/1445)).

`Capability.PRIVATE_BROADCAST` is never declared either: `cli.py`
registers no `-privatebroadcast`, and neither `getprivatebroadcastinfo`
nor `abortprivatebroadcast` names a callback in
`src/btclib_node/rpc/callbacks.py`'s own dispatch table, measured at the
released `2026.9.24` (`422d2640`) and at `main` (`d4559960`) alike,
`privatebroadcast` being a `-debug` category at `main`.

`Capability.STARTUP_NOTIFY` is never declared either: `cli.py` registers
no `-startupnotify`, measured at the released `2026.9.24` (`422d2640`)
and at `main` (`d4559960`) alike
([ISS btclib-node#1449](https://github.com/btclib-org/btclib-node/issues/1449)).

`Capability.LOAD_BLOCK` is never declared either: no source file
names `loadblock`, measured at the released `2026.9.24` (`422d2640`)
and at `main` (`ecb9b190`) alike. Reading Core's block files is left
out by decision, the node taking the same blocks over p2p
([ISS btclib-node#573](https://github.com/btclib-org/btclib-node/issues/573)).

`Capability.LISTEN_ADDRESS` is declared per instance, by
`_binds_address`'s own probe: a build whose `cli.py` accepts `-bind`. The
released `2026.10.4` and `main` (`ab2a63cc`) both do.

`Capability.MAX_TIP_AGE` is never declared either: `cli.py` registers
no `-maxtipage`, the age being `constants.py`'s own `MAX_TIP_AGE` of a
day, measured at the released `2026.9.24` (`422d2640`) and at `main`
(`9ae620c2`) alike
([ISS btclib-node#1474](https://github.com/btclib-org/btclib-node/issues/1474)).

`Capability.PEER_BLOCK_FILTERS` is never declared either: `cli.py`
registers no `-peerblockfilters`, and `p2p/callbacks.py` answers
`getcfilters`, `getcfheaders` and `getcfcheckpt` for every peer while
`p2p/connection.py` signals `NODE_COMPACT_FILTERS` to every one, with no
option to turn either off, measured at the released `2026.9.24`
(`422d2640`) and at `main` (`9ae620c2`) alike
([ISS btclib-node#1395](https://github.com/btclib-org/btclib-node/issues/1395)).

`Capability.MINIMUM_CHAIN_WORK` is never declared either: `cli.py`
registers no `-minimumchainwork`, the floor being the chain's own
`minimum_chain_work` (`btclib.consensus`), measured at the released
`2026.9.24` (`422d2640`) and at `main` (`93c1d066`) alike
([ISS btclib-node#1500](https://github.com/btclib-org/btclib-node/issues/1500)).

`Capability.PACKAGE_ACCEPTANCE` is declared per instance, by
`_serves_submitpackage`'s own probe: a build whose `rpc/callbacks.py` names
`submitpackage` in its public `callbacks`
([ISS btclib-node#1494](https://github.com/btclib-org/btclib-node/issues/1494)).
A build that names none does not gain the capability.

`Capability.CLUSTER_REPLACEMENT` is declared per instance, by
`_replaces_by_cluster`'s own probe: a build whose `btclib_node.mempool.Mempool`
has `check_package_replacement`, which counts a replacement's conflicts by
cluster as Core's `ReplacementChecks` does -- the released `2026.10.18`, and
`main` from `cd82fd74`, the change that closed
[ISS btclib-node#1334](https://github.com/btclib-org/btclib-node/issues/1334).
The released `2026.10.8` has no such method, so an instance built against it
does not gain the capability.

`Capability.MIN_RELAY_TX_FEE` is declared per instance, by
`_sets_min_relay_fee`'s own probe: a build whose `cli.py` registers
`-minrelaytxfee` -- `v2026.10.4` (`f1732715`) and `main` (`6777a6a8`), from
btclib-node PR 1452 (`88f5c894`), the option
[ISS btclib-node#1332](https://github.com/btclib-org/btclib-node/issues/1332)
asked for. `v2026.9.24` (`422d2640`) registers none, its
`Config.min_relay_feerate` taking no flag, so an instance built against it
does not gain the capability.

`Capability.PERMIT_BARE_MULTISIG` is declared per instance, by
`_permits_bare_multisig`'s own probe: a build whose `cli.py` registers
`-permitbaremultisig` -- `v2026.10.4` (`f1732715`) and `main` (`6777a6a8`),
from btclib-node PR 1599 (`9fe1bfd9`), the option
[ISS btclib-node#1497](https://github.com/btclib-org/btclib-node/issues/1497)
asked for. `v2026.9.24` (`422d2640`) registers none, so an
instance built against it does not gain the capability.

`Capability.ALERT_NOTIFY` is never declared either: no source file
names `alertnotify`, measured at the released `2026.9.24` (`422d2640`)
and at `main` (`93c1d066`) alike
([ISS btclib-node#1475](https://github.com/btclib-org/btclib-node/issues/1475)).

`Capability.BLOCK_NOTIFY` and `Capability.SHUTDOWN_NOTIFY` are never
declared either: no file names `blocknotify` or `shutdownnotify`,
measured at the released `2026.9.24` (`422d2640`) and at `main`
(`84677942`) alike
([ISS btclib-node#1519](https://github.com/btclib-org/btclib-node/issues/1519)).

`Capability.SETTINGS_FILE` is never declared either: `cli.py` registers
no `-settings`, and no file under `src/` reads or writes a
`settings.json`, measured at the released `2026.9.24` (`422d2640`) and
at `main` (`84677942`) alike
([ISS btclib-node#1523](https://github.com/btclib-org/btclib-node/issues/1523)).

`Capability.CHAIN_TIPS` is declared per instance, by `_serves_chain_tips`'s
own probe: a build whose `rpc/callbacks.py` names `getchaintips` in its
public `callbacks`, as `main` (`e471c576`) does. The released `2026.9.24`
(`422d2640`) names no such callback, so an instance built against it does
not gain the capability.

`Capability.ASSUME_VALID` is never declared: no file names
`assumevalid`, measured at the released `2026.9.24` (`422d2640`) and at
`main` (`26ec9ec5`) alike
([ISS btclib-node#1576](https://github.com/btclib-org/btclib-node/issues/1576)).

`Capability.INCREMENTAL_RELAY_FEE` is never declared: `cli.py` registers
no `-incrementalrelayfee`, measured at the released `2026.9.24`
(`422d2640`) and at `main` (`eb18985c`) alike
([ISS btclib-node#1596](https://github.com/btclib-org/btclib-node/issues/1596)).

Each capability `_RPC_CAPABILITIES` names is declared per instance, by
`_served_rpcs`'s own probe: where the build's `rpc/callbacks.py` names every RPC
`_RPC_CAPABILITIES` lists for it. The released `2026.9.24` (`422d2640`) names
none of them, and the released `2026.10.30` (`30e9573`) and `main` (`995a2b46`),
whose `src/` is the same, name all of them
([ISS 547](https://github.com/btclib-org/functional-tests/issues/547)).
What each capability asks beyond its RPCs' names was measured at `2026.10.30`.
`addconnection` dials each of `outbound-full-relay`, `block-relay-only`,
`addr-fetch` and `feeler` (its `_ADDCONNECTION_TYPES`), and refuses any chain
but regtest, so `TYPED_OUTBOUND` is in `_REGTEST_ONLY` below. `getblocktemplate`
takes BIP23's `proposal` mode (`rpc/mining.py`'s `_proposal`).
`setnetworkactive` drops every held connection on a `false`
(`P2pManager.set_network_active`). `scantxoutset` reads descriptors
(`rpc/utxo_set.py`), and `dumptxoutset` takes `rollback` (`rpc/snapshot.py`).
Run live, `getmempoolcluster` answered a three-transaction chain's cluster by
chunk, `getmempoolfeeratediagram` its diagram and `getmempoolinfo` an `optimal`;
`getmempoolancestors`, bare and verbose, `getmempooldescendants` and
`gettxspendingprevout` answered the chain's links. Where a row of `TF2.md` fails
on one of these capabilities, its entry there names its issue.

`Capability.PEER_PERMISSIONS` is declared per instance, by
`_reads_whitelist`'s own probe: a build whose `cli.py` registers
`-whitelist`
([ISS btclib-node#1320](https://github.com/btclib-org/btclib-node/issues/1320))
and `-whitebind`
([ISS btclib-node#1625](https://github.com/btclib-org/btclib-node/issues/1625)),
as the released `2026.10.30` and `main` (`98f2662e`) do. The released
`2026.9.24` (`422d2640`) registers neither, so an instance built against
it does not gain the capability.

`Capability.BLOCKS_DIR` and `Capability.UTXO_SET_INFO` are class-wide:
`cli.py` registers `-blocksdir` and `rpc/callbacks.py` names
`gettxoutsetinfo`, measured at the released `2026.9.24` (`422d2640`)
and at `2026.10.8` alike.

`Capability.MEMPOOL_ENTRY` is declared per instance, by
`_serves_mempool_entry`'s own probe: a build whose `rpc/callbacks.py`
names `getmempoolentry`, as `2026.10.8` does. The released
`2026.9.24` (`422d2640`) names no such callback, so an instance built
against it does not gain the capability.

`Capability.INCLUDE_CONF` is declared per instance too, by
`_reads_includeconf`'s own probe: a build whose `cli.py` reads
`-noincludeconf`, as `2026.10.8` does. The released `2026.9.24`
(`422d2640`) refuses it, and ignores an `includeconf` nested in an
included file without the warning Core prints, so an instance built
against it does not gain the capability.

`Capability.ADDRESS_TABLES` is never declared: `cli.py` registers neither
`-test` nor `-checkaddrman`
([ISS btclib-node#2175](https://github.com/btclib-org/btclib-node/issues/2175)),
and `rpc/callbacks.py` names neither `getrawaddrman`
([ISS btclib-node#2126](https://github.com/btclib-org/btclib-node/issues/2126))
nor `getaddrmaninfo`
([ISS btclib-node#2121](https://github.com/btclib-org/btclib-node/issues/2121)),
measured at the released `2026.10.30` (`30e9573`) and at `main` (`995a2b46`)
alike.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
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
    "BtclibNodeAdapter",
]

# btclib-node's own build before ISS btclib-node#1070 (`422d2640`, what
# PyPI's `2026.9.24` release installs) checks no credential at all
# and binds RPC to 127.0.0.1 only -- ISS btclib-org/btclib#2135's own
# census, quoted in ISS btclib-org/btclib#2220 -- so a placeholder is
# what `bitcoin_core_rpc.BitcoinCoreRpcClient` is given for that build
# instead of one the constructor refuses to be
# built with none of: `_writes_auth_cookie` below is what decides whether
# it is used at all, rather than the cookie every later build writes.
_RPC_USER = "tf2"
_RPC_PASSWORD = "tf2"  # noqa: S105 -- ignored by a pre-#1070 build; see above

# each chain `-chain=` names, in Core's vocabulary, and the subdirectory of
# `-datadir` the node writes its cookie and its log into: `chains.py`'s own
# `name` of the chain `cli.py`'s `_CHAIN_ALIASES` resolves it to, measured at
# the released `2026.9.24` and at `main` (`35b26d2e`) alike. Neither names
# `testnet4`.
_CHAIN_DIRS = {
    "main": "mainnet",
    "test": "testnet",
    "signet": "signet",
    "regtest": "regtest",
}


def _config_probe_passes(executable: str, probe: str) -> bool:
    """Return whether `probe`, run by `executable`, exits zero.

    `build_config` writes `settings.json` under `~/.btclib` where no
    `-datadir` is given. Probes run at once by several xdist workers
    rename one temporary file over each other, and one of them fails.
    Each probe gets a home of its own.

    :param executable: the interpreter `btclib-node` is installed into.
    :param probe: the program `executable` runs with `-c`.
    """
    with tempfile.TemporaryDirectory() as home:
        result = subprocess.run(  # noqa: S603
            [executable, "-c", probe],
            check=False,
            capture_output=True,
            env={**os.environ, "HOME": home, "USERPROFILE": home},
        )
    return result.returncode == 0


@lru_cache
def _writes_auth_cookie(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node writes an RPC cookie.

    ISS btclib-node#1070 (`18b6ae1e`) landed Core-style RPC authentication
    -- `-rpcuser`/`-rpcpassword`, the cookie `rpc/auth.py`'s own
    `RpcAuth.start` writes to `<data_dir>/regtest/.cookie` unless one of
    them is given, and `-rpcwhitelist` -- and gained the module
    `btclib_node.rpc.auth` along with it, absent from every build before.
    Its presence is what this checks, matching the probe
    `tests/integration/conftest.py`'s own `btclib_node_python` fixture
    already makes for the package itself. A build before it (`382a29fb`)
    checks no credential at all and answers unrecognised any
    `-rpcuser`/`-rpcpassword` given on its own argv -- confirmed live,
    exit `2` there before the node's RPC ever starts.

    A probe of the interpreter/version pair, never of a running node: no
    port is bound and no data directory is created. Cached per
    `executable`, because the answer is a fact about the install rather
    than about any one adapter instance, and `_rpc_client` below is
    called fresh on every access to `.rpc`.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", "import btclib_node.rpc.auth"],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


@lru_cache
def _evicts_inbound(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node evicts inbound peers.

    `import btclib_node.p2p.eviction` exiting zero, in the standing of
    `_writes_auth_cookie` above: no port bound, no data directory
    created, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", "import btclib_node.p2p.eviction"],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# `tf2` is no `<user>:<salt>$<hash>`, so `RpcAuthEntry.parse` refuses it
# wherever it survives the command line. Since
# [ISS btclib-node#1210](https://github.com/btclib-org/btclib-node/issues/1210),
# that refusal is kept on the returned config as `rpc_auth_invalid`
# rather than raised, so `build_config` returns whether or not
# `-norpcauth` discarded the value: the probe below exits on that field
# instead of on the return itself, `getattr` defaulting `False` for a
# build that predates the field and so raises wherever the value
# survives. `-noconf` keeps any `bitcoin.conf` out of it.
_NEGATION_PROBE = (
    "from btclib_node.cli import build_config; "
    'config = build_config(["-regtest", "-noconf", "-rpcauth=tf2", "-norpcauth"]); '
    'raise SystemExit(1 if getattr(config, "rpc_auth_invalid", False) else 0)'
)


@lru_cache
def _negates_rpcauth(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node negates `-rpcauth`.

    Asks the build's own `cli.build_config` -- which reads the command
    line as `main` does, and takes no lock -- to
    read `-rpcauth` followed by its negation, and answers whether the
    resulting config's `rpc_auth_invalid`, where the build has one, is
    false -- and where it has none, whether it returned: `_NEGATION_PROBE`
    above is why exiting zero means the value was discarded rather than
    merely accepted alongside `-norpcauth` on the command line. A build
    with no generic negation refuses the argument and exits nonzero.
    Otherwise in the standing of `_writes_auth_cookie` above: no port
    bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _NEGATION_PROBE)


# `update_chain` handed a node still at `SyncingHeaders`: a build that
# gates on the status never reads `chainstate` and exits nonzero, whether
# it returns or trips on the stub further on; a build that does not asks
# `get_first_candidate` first, which exits 0 on the spot.
_SOLO_CONNECT_PROBE = """\
from types import SimpleNamespace
from btclib_node.constants import NodeStatus
from btclib_node.main import update_chain
def reached(): raise SystemExit(0)
index = SimpleNamespace(get_first_candidate=reached)
chainstate = SimpleNamespace(block_index=index)
update_chain(SimpleNamespace(status=NodeStatus.SyncingHeaders, chainstate=chainstate))
raise SystemExit(1)
"""


@lru_cache
def _connects_alone(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node connects with no peer.

    Asks the build's own `main.update_chain`, public in `main`'s own
    `__all__`, whether it looks for a block to connect while
    `node.status` is `SyncingHeaders`, the status a node with no peer
    never leaves: `_SOLO_CONNECT_PROBE` above is how the two answers are
    told apart. Otherwise in the standing of `_writes_auth_cookie` above:
    no node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _SOLO_CONNECT_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where the dispatch table holds each of `Capability.BAN`'s RPCs
_BAN_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
ban_rpcs = {"setban", "listbanned", "clearbanned"}
raise SystemExit(0 if ban_rpcs <= callbacks.keys() else 1)
"""


@lru_cache
def _serves_ban_list(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node answers the ban RPCs.

    Asks the build's own `rpc.callbacks.callbacks`, public in that
    module's own `__all__`, whether it names `setban`, `listbanned` and
    `clearbanned`: `_BAN_PROBE` above. Otherwise in the standing of
    `_writes_auth_cookie` above: no node started, no port bound, and
    cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _BAN_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where `-minrelaytxfee`, in Core's BTC/kvB, sets the floor
# `Config.min_relay_feerate` holds in sat/kvB; a build registering no such
# flag refuses it and exits nonzero. `-noconf` keeps any `bitcoin.conf` out
_MIN_RELAY_FEE_PROBE = """\
from btclib_node.cli import build_config
config = build_config(["-regtest", "-noconf", "-minrelaytxfee=0.00000010"])
raise SystemExit(0 if config.min_relay_feerate.sats_per_kvbyte == 10 else 1)
"""


@lru_cache
def _sets_min_relay_fee(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node reads `-minrelaytxfee`.

    Asks the build's own `cli.build_config`, as `_negates_rpcauth` above
    does, to read the flag, and answers whether the resulting config's
    `min_relay_feerate` is the rate it names: `_MIN_RELAY_FEE_PROBE`
    above. Otherwise in the standing of `_writes_auth_cookie` above: no
    node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _MIN_RELAY_FEE_PROBE)


# exits 0 only where `-permitbaremultisig=0` turns
# `Config.permit_bare_multisig` off; a build registering no such flag
# refuses it and exits nonzero. `-noconf` keeps any `bitcoin.conf` out of it
_BARE_MULTISIG_PROBE = """\
from btclib_node.cli import build_config
config = build_config(["-regtest", "-noconf", "-permitbaremultisig=0"])
raise SystemExit(0 if config.permit_bare_multisig is False else 1)
"""


@lru_cache
def _permits_bare_multisig(executable: str) -> bool:
    """Return whether `executable`'s btclib-node reads `-permitbaremultisig`.

    Asks the build's own `cli.build_config`, as `_sets_min_relay_fee`
    above does, to read the flag, and answers whether the resulting
    config's `permit_bare_multisig` is off: `_BARE_MULTISIG_PROBE` above.
    No node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _BARE_MULTISIG_PROBE)


# exits 0 only where `-nov2transport` turns `Config.v2transport` off; a
# build registering no such flag refuses it and exits nonzero. `-noconf`
# keeps any `bitcoin.conf` out of it
_V2_PROBE = """from btclib_node.cli import build_config
config = build_config(["-regtest", "-noconf", "-nov2transport"])
raise SystemExit(0 if config.v2transport is False else 1)
"""


@lru_cache
def _speaks_v2(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node speaks BIP324.

    Asks the build's own `cli.build_config`, as `_sets_min_relay_fee`
    above does, to read `-nov2transport`, and answers whether the
    resulting config's `v2transport` is false: `_V2_PROBE` above.
    Otherwise in the standing of `_writes_auth_cookie` above: no node
    started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _V2_PROBE)


# exits 0 only where `-v1transport=1` is accepted and `Config.v1transport`
# reads true; a build registering no such flag refuses it and exits
# nonzero. `-noconf` keeps any `bitcoin.conf` out of it
_V1_PROBE = """from btclib_node.cli import build_config
config = build_config(["-regtest", "-noconf", "-v1transport=1"])
raise SystemExit(0 if config.v1transport is True else 1)
"""


@lru_cache
def _accepts_v1transport(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node has `-v1transport`.

    Asks the build's own `cli.build_config`, as `_speaks_v2` above does,
    to read `-v1transport=1`: `_V1_PROBE` above. Cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _V1_PROBE)


# exits 0 only where `-testactivationheight=segwit@5` moves
# `Config.chain.consensus.segwit_height` off regtest's own 0; a build
# registering no such flag refuses it and exits nonzero. `-noconf` keeps any
# `bitcoin.conf` out of it
_ACTIVATION_HEIGHT_PROBE = """\
from btclib_node.cli import build_config
config = build_config(["-regtest", "-noconf", "-testactivationheight=segwit@5"])
raise SystemExit(0 if config.chain.consensus.segwit_height == 5 else 1)
"""


@lru_cache
def _moves_activation_heights(executable: str) -> bool:
    """Return whether `executable`'s btclib-node reads `-testactivationheight`.

    Asks the build's own `cli.build_config`, as `_speaks_v2` above does,
    to read the flag, and answers whether the resulting config's chain
    activates segwit at the height it names: `_ACTIVATION_HEIGHT_PROBE`
    above. No node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _ACTIVATION_HEIGHT_PROBE)


# exits 0 only where the dispatch table holds `Capability.CHAIN_TIPS`'s RPC
_CHAIN_TIPS_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
raise SystemExit(0 if "getchaintips" in callbacks else 1)
"""


@lru_cache
def _serves_chain_tips(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node answers `getchaintips`.

    Asks the build's own `rpc.callbacks.callbacks`, as `_serves_ban_list`
    above does, whether it names `getchaintips`: `_CHAIN_TIPS_PROBE`
    above. Otherwise in the standing of `_writes_auth_cookie` above: no
    node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _CHAIN_TIPS_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where the dispatch table holds `Capability.DISCONNECT`'s RPC
_DISCONNECT_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
raise SystemExit(0 if "disconnectnode" in callbacks else 1)
"""


@lru_cache
def _serves_disconnect(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node answers `disconnectnode`.

    Asks the build's own `rpc.callbacks.callbacks`, as `_serves_chain_tips`
    above does, whether it names `disconnectnode`: `_DISCONNECT_PROBE`
    above. Otherwise in the standing of `_writes_auth_cookie` above: no
    node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _DISCONNECT_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where the dispatch table holds `PACKAGE_ACCEPTANCE`'s RPC
_SUBMITPACKAGE_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
raise SystemExit(0 if "submitpackage" in callbacks else 1)
"""


@lru_cache
def _serves_submitpackage(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node answers `submitpackage`.

    Asks the build's own `rpc.callbacks.callbacks`, as `_serves_disconnect`
    above does, whether it names `submitpackage`: `_SUBMITPACKAGE_PROBE`
    above. Otherwise in the standing of `_writes_auth_cookie` above: no
    node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _SUBMITPACKAGE_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where `Mempool` has the check that counts a replacement's
# conflicts by cluster
_CLUSTER_REPLACEMENT_PROBE = """\
from btclib_node.mempool import Mempool
raise SystemExit(0 if hasattr(Mempool, "check_package_replacement") else 1)
"""


@lru_cache
def _replaces_by_cluster(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node replaces by cluster.

    Asks the build's own `mempool.Mempool` whether it has
    `check_package_replacement`: `_CLUSTER_REPLACEMENT_PROBE` above.
    Otherwise in the standing of `_writes_auth_cookie` above: no node
    started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _CLUSTER_REPLACEMENT_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where the dispatch table holds `Capability.ORPHANAGE`'s RPC
_GETORPHANTXS_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
raise SystemExit(0 if "getorphantxs" in callbacks else 1)
"""


@lru_cache
def _serves_getorphantxs(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node answers `getorphantxs`.

    Asks the build's own `rpc.callbacks.callbacks`, as `_serves_submitpackage`
    above does, whether it names `getorphantxs`: `_GETORPHANTXS_PROBE`
    above. Otherwise in the standing of `_writes_auth_cookie` above: no
    node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _GETORPHANTXS_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where the dispatch table holds `Capability.MEMPOOL_ENTRY`'s RPC
_MEMPOOL_ENTRY_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
raise SystemExit(0 if "getmempoolentry" in callbacks else 1)
"""


@lru_cache
def _serves_mempool_entry(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node answers `getmempoolentry`.

    Asks the build's own `rpc.callbacks.callbacks`, as `_serves_getorphantxs`
    above does, whether it names `getmempoolentry`: `_MEMPOOL_ENTRY_PROBE`
    above. Otherwise in the standing of `_writes_auth_cookie` above: no
    node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _MEMPOOL_ENTRY_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where the dispatch table holds `Capability.CLOCK`'s RPC
_SETMOCKTIME_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
raise SystemExit(0 if "setmocktime" in callbacks else 1)
"""


@lru_cache
def _serves_setmocktime(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node answers `setmocktime`.

    Asks the build's own `rpc.callbacks.callbacks`, as `_serves_mempool_entry`
    above does, whether it names `setmocktime`: `_SETMOCKTIME_PROBE` above.
    Otherwise in the standing of `_writes_auth_cookie` above: no node
    started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _SETMOCKTIME_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# exits 0 only where the dispatch table holds each of `Capability.GENERATE`'s
# RPCs
_GENERATE_PROBE = """\
from btclib_node.rpc.callbacks import callbacks
generate_rpcs = {"generatetoaddress", "generateblock"}
raise SystemExit(0 if generate_rpcs <= callbacks.keys() else 1)
"""


@lru_cache
def _serves_generate(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node builds blocks on request.

    Asks the build's own `rpc.callbacks.callbacks`, as `_serves_ban_list`
    above does, whether it names `generatetoaddress` and `generateblock`:
    `_GENERATE_PROBE` above. Otherwise in the standing of
    `_writes_auth_cookie` above: no node started, no port bound, and
    cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _GENERATE_PROBE],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


# prints the names the build's dispatch table holds, as a JSON list
_SERVED_RPCS_PROBE = """\
import json
from btclib_node.rpc.callbacks import callbacks
print(json.dumps(sorted(callbacks)))
"""


@lru_cache
def _served_rpcs(executable: str) -> frozenset[str]:
    """Return the RPC names `executable`'s own btclib-node dispatches.

    Reads the build's own `rpc.callbacks.callbacks`, as `_serves_ban_list`
    above does, but answers every name it holds rather than whether it
    holds some: `_SERVED_RPCS_PROBE` above. A build that cannot be
    imported answers none. Otherwise in the standing of
    `_writes_auth_cookie` above: no node started, no port bound, and
    cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    probe = subprocess.run(  # noqa: S603
        [executable, "-c", _SERVED_RPCS_PROBE],
        check=False,
        capture_output=True,
        encoding="utf-8",
    )
    if probe.returncode != 0:
        return frozenset()
    return frozenset(json.loads(probe.stdout))


# each capability a build declares where `_served_rpcs` holds every RPC named
# here: the module docstring says what was measured beyond the names
_RPC_CAPABILITIES = {
    Capability.TYPED_OUTBOUND: frozenset({"addconnection"}),
    Capability.SUSPEND_NETWORK: frozenset({"setnetworkactive"}),
    Capability.SIGN_RAW_TRANSACTION: frozenset(
        {"signrawtransactionwithkey", "combinerawtransaction"}
    ),
    Capability.INVALIDATE_BLOCK: frozenset({"invalidateblock", "reconsiderblock"}),
    Capability.SCAN_UTXO_SET: frozenset({"scantxoutset"}),
    Capability.BLOCK_PROPOSAL: frozenset({"getblocktemplate"}),
    Capability.KNOWN_ADDRESSES: frozenset({"addpeeraddress", "getnodeaddresses"}),
    Capability.RPC_INFO: frozenset({"getrpcinfo"}),
    Capability.CLUSTER_LINEARIZATION: frozenset(
        {"getmempoolcluster", "getmempoolfeeratediagram"}
    ),
    Capability.MEMPOOL_GRAPH: frozenset(
        {"getmempoolancestors", "getmempooldescendants", "gettxspendingprevout"}
    ),
    Capability.PRECIOUS_BLOCK: frozenset({"preciousblock"}),
    Capability.SIGN_MESSAGE_WITH_PRIVKEY: frozenset(
        {"signmessagewithprivkey", "verifymessage"}
    ),
    Capability.ESTIMATE_SMART_FEE: frozenset({"estimatesmartfee", "estimaterawfee"}),
    Capability.DUMP_UTXO_SET: frozenset({"dumptxoutset"}),
}


# exits 0 only where `-noincludeconf` is read: a build registering no
# negated `-includeconf` refuses it and exits nonzero. `-noconf` keeps any
# `bitcoin.conf` out of it
_INCLUDE_CONF_PROBE = """\
from btclib_node.cli import build_config
build_config(["-regtest", "-noconf", "-noincludeconf"])
"""


@lru_cache
def _reads_includeconf(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node honours `includeconf`.

    Asks the build's own `cli.build_config`, as `_binds_address` below
    does, to read `-noincludeconf`: `_INCLUDE_CONF_PROBE` above. The
    builds that refuse it are the ones that also ignore an
    `includeconf` nested in an included file without a warning.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _INCLUDE_CONF_PROBE)


# exits 0 only where `-bind` is read into `Config.bind`; a build registering
# no such flag refuses it and exits nonzero. `-noconf` keeps any
# `bitcoin.conf` out of it
_BIND_PROBE = """\
from btclib_node.cli import build_config
config = build_config(["-regtest", "-noconf", "-bind=127.0.0.1:18555"])
raise SystemExit(0 if config.bind else 1)
"""


@lru_cache
def _binds_address(executable: str) -> bool:
    """Return whether `executable`'s own btclib-node reads `-bind`.

    Asks the build's own `cli.build_config`, as `_permits_bare_multisig`
    above does, to read the flag, and answers whether the resulting
    config's `bind` holds a value: `_BIND_PROBE` above. No node started,
    no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _BIND_PROBE)


# exits 0 only where `-whitelist` and `-whitebind` are read into
# `Config.whitelist` and `Config.whitebind`; a build missing either flag
# refuses it and exits nonzero. `-noconf` keeps any `bitcoin.conf` out of it
_WHITELIST_PROBE = """\
from btclib_node.cli import build_config
whitelist = "-whitelist=noban@127.0.0.1"
whitebind = "-whitebind=noban@127.0.0.1:18556"
config = build_config(["-regtest", "-noconf", whitelist, whitebind])
raise SystemExit(0 if config.whitelist and config.whitebind else 1)
"""


@lru_cache
def _reads_whitelist(executable: str) -> bool:
    """Return whether `executable`'s btclib-node reads the permission flags.

    Asks the build's own `cli.build_config`, as `_binds_address` above
    does, to read both flags, and answers whether the resulting config's
    `whitelist` and `whitebind` both hold a value: `_WHITELIST_PROBE`
    above. No node started, no port bound, and cached per executable.

    :param executable: the interpreter `btclib-node` is installed into.
    """
    return _config_probe_passes(executable, _WHITELIST_PROBE)


# the facts true of every build: `connect`, and the two the module
# docstring names as class-wide
_EVERY_BUILD = frozenset(
    {
        Capability.CONNECT,
        Capability.BLOCKS_DIR,
        Capability.UTXO_SET_INFO,
    }
)

# the probed capabilities a node on any chain but regtest lacks: `MiniWallet`
# builds regtest's own blocks alone; `setmocktime` refuses any other chain
# (`rpc/callbacks.py`'s own `set_mock_time`); `generatetoaddress` and
# `generateblock` give up after `maxtries` nonces (`rpc/mining.py`), a bound
# regtest's own target is met within and another chain's is not in practice;
# `addconnection` refuses any other chain (`rpc/callbacks.py`'s own
# `add_connection`)
_REGTEST_ONLY = frozenset(
    {
        Capability.MINE,
        Capability.CLOCK,
        Capability.GENERATE,
        Capability.TYPED_OUTBOUND,
    }
)


class BtclibNodeAdapter(NodeAdapter):
    """A `btclib-node`, run as `python -m btclib_node`.

    Not the console script `pip install btclib-node` also provides:
    `cli.py`'s own module docstring is where the reason not to run that
    entry point directly from a spawning process is argued --
    `ReimportedMainProcessError` reaching every path but the one
    `python -m` and its own `__main__.py` exempt.

    `chains` is every chain `_CHAIN_DIRS` names, `testnet4` not among
    them. On any chain but regtest an instance drops `_REGTEST_ONLY`'s
    capabilities.
    """

    capabilities: AbstractSet[Capability] = _EVERY_BUILD
    chains: AbstractSet[str] = frozenset(_CHAIN_DIRS)

    @override
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
        """Construct the adapter, then add each probed capability that holds.

        `super().__init__` runs first -- `NodeAdapter.__init__`'s own
        `_check_extra_args(self._command(), extra_args)` needs
        `self._executable` set before `_command` above can be called, and
        that guard is unaffected by which capabilities this instance ends
        up declaring. `_writes_auth_cookie` is then the same probe
        `_rpc_client` below already makes for cookie authentication, not
        a second one: the module docstring's own paragraph on
        `Capability.RPC_AUTH_CONFIG` is why one probe answers both,
        `_negates_rpcauth` answers `Capability.RPC_AUTH_NEGATION`,
        `_evicts_inbound` answers `Capability.INBOUND_EVICTION`,
        `_connects_alone` answers `Capability.MINE`,
        `_serves_ban_list` answers `Capability.BAN`,
        `_sets_min_relay_fee` answers `Capability.MIN_RELAY_TX_FEE`,
        `_permits_bare_multisig` answers `Capability.PERMIT_BARE_MULTISIG`,
        `_serves_chain_tips` answers `Capability.CHAIN_TIPS`,
        `_serves_disconnect` answers `Capability.DISCONNECT`,
        `_serves_submitpackage` answers `Capability.PACKAGE_ACCEPTANCE`,
        `_serves_getorphantxs` answers `Capability.ORPHANAGE`,
        `_replaces_by_cluster` answers `Capability.CLUSTER_REPLACEMENT`,
        `_binds_address` answers `Capability.LISTEN_ADDRESS`,
        `_reads_whitelist` answers `Capability.PEER_PERMISSIONS`,
        `_serves_mempool_entry` answers `Capability.MEMPOOL_ENTRY`,
        `_reads_includeconf` answers `Capability.INCLUDE_CONF`,
        `_serves_setmocktime` answers `Capability.CLOCK`,
        `_serves_generate` answers `Capability.GENERATE`,
        `_speaks_v2` answers `Capability.V2TRANSPORT`,
        `_moves_activation_heights` answers
        `Capability.TEST_ACTIVATION_HEIGHT`, and
        `_served_rpcs` answers each capability `_RPC_CAPABILITIES` names.
        The class-level `capabilities` -- `_EVERY_BUILD` -- is left
        untouched where every probe answers `False`. A `chain` other than
        regtest drops `_REGTEST_ONLY`'s capabilities whatever their probes
        answer, and chain `main` drops `Capability.TEST_ACTIVATION_HEIGHT`
        too: `cli.py` refuses `-testactivationheight` on mainnet alone.
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
        probed = {
            capability
            for probe, capability in (
                (_writes_auth_cookie, Capability.RPC_AUTH_CONFIG),
                (_negates_rpcauth, Capability.RPC_AUTH_NEGATION),
                (_evicts_inbound, Capability.INBOUND_EVICTION),
                (_serves_ban_list, Capability.BAN),
                (_sets_min_relay_fee, Capability.MIN_RELAY_TX_FEE),
                (_permits_bare_multisig, Capability.PERMIT_BARE_MULTISIG),
                (_serves_chain_tips, Capability.CHAIN_TIPS),
                (_serves_disconnect, Capability.DISCONNECT),
                (_serves_submitpackage, Capability.PACKAGE_ACCEPTANCE),
                (_serves_getorphantxs, Capability.ORPHANAGE),
                (_replaces_by_cluster, Capability.CLUSTER_REPLACEMENT),
                (_binds_address, Capability.LISTEN_ADDRESS),
                (_reads_whitelist, Capability.PEER_PERMISSIONS),
                (_serves_mempool_entry, Capability.MEMPOOL_ENTRY),
                (_reads_includeconf, Capability.INCLUDE_CONF),
                (_serves_setmocktime, Capability.CLOCK),
                (_serves_generate, Capability.GENERATE),
                (_speaks_v2, Capability.V2TRANSPORT),
                (_moves_activation_heights, Capability.TEST_ACTIVATION_HEIGHT),
                (_connects_alone, Capability.MINE),
            )
            if probe(executable)
        }
        served = _served_rpcs(executable)
        probed |= {
            capability
            for capability, rpcs in _RPC_CAPABILITIES.items()
            if rpcs <= served
        }
        if chain != "regtest":
            probed -= _REGTEST_ONLY
        if chain == "main":
            probed.discard(Capability.TEST_ACTIVATION_HEIGHT)
        if probed:
            self.capabilities = type(self).capabilities | probed

    @override
    def _command(self) -> list[str]:
        """Return btclib-node's own argv, over `python -m btclib_node`.

        `self._executable` is the interpreter (`sys.executable` of
        whichever environment `btclib-node` is installed into), never
        the console script -- the module docstring is why. Carries no
        `-rpcuser`/`-rpcpassword`: a build before ISS btclib-node#1070
        refuses either flag outright, so the credential is `_rpc_client`
        below's alone, never this argv's.

        `-chain` names the chain. On any chain but regtest, which has no
        seed to reach, `-connect=0` keeps the node from reaching the real
        network: the node's own `P2pManager` asks no DNS or fixed seed and
        draws no outbound connection once `-connect` is given, `0`
        dialling nobody. `-listen=1` keeps the `-port` listener `-connect`
        would turn off by default, the listener every inbound connection a
        test makes dials. That listener binds every interface, on every
        chain, this node having no `-bind` to narrow it
        (ISS btclib-org/btclib-node#1257).

        `-v1transport=1` is added where `_accepts_v1transport` holds,
        since a `Peer` speaks v1 unless asked for v2 and btclib-node
        refuses v1 under `-v1transport=0`; a build without the flag
        refuses it, so gets nothing.
        """
        isolation = [] if self._chain == "regtest" else ["-connect=0", "-listen=1"]
        v1 = ["-v1transport=1"] if _accepts_v1transport(self._executable) else []
        return [
            self._executable,
            "-m",
            "btclib_node",
            f"-chain={self._chain}",
            f"-datadir={self._datadir}",
            f"-rpcport={self._rpc_port}",
            "-rpcbind=127.0.0.1",
            f"-port={self._p2p_port}",
            *isolation,
            *v1,
        ]

    @property
    def _chain_dir(self) -> Path:
        """Return the directory of this node's cookie and log: `_CHAIN_DIRS`."""
        return self._datadir / _CHAIN_DIRS[self._chain]

    @override
    def _rpc_client(self) -> BitcoinCoreRpcClient:
        """Return a client authenticating the way this build actually checks.

        `rpc_auth` (`NodeAdapter.__init__`) first, where the caller named
        one: a node started with `-rpcuser`/`-rpcpassword` or
        `-norpccookiefile` (`extra_args`) writes no cookie at all, on a
        build past
        [ISS btclib-node#1070](https://github.com/btclib-org/btclib-node/issues/1070)
        exactly as bitcoind does not either, so nothing here can wait on
        one -- the caller that put either flag on the command line already
        knows the credential to authenticate with instead. Absent that,
        cookie authentication, `BitcoindAdapter`'s own mechanism, where
        `_writes_auth_cookie` finds the build writes one, in `_chain_dir`.
        A build with no
        Core-style RPC authentication at all is given the placeholder
        credential instead, which it never checks. Every path builds its
        client over `NodeAdapter._rpc_transport`, this adapter's own
        connections, `--tracerpc` included, and bounds each call by
        `timeout_factor.rpc_client_timeout`, read when the client is built,
        matching `BitcoindAdapter`.
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
        if _writes_auth_cookie(self._executable):
            return BitcoinCoreRpcClient(
                url,
                cookie_path=self._chain_dir / ".cookie",
                timeout=timeout,
                transport=transport,
            )
        return BitcoinCoreRpcClient(
            url,
            user=_RPC_USER,
            password=_RPC_PASSWORD,
            timeout=timeout,
            transport=transport,
        )

    @property
    def log_path(self) -> Path:
        """Return this node's own `history.log`, the disk family's own fact.

        Not `debug_log_path`: `BitcoindAdapter`'s own name is Core's file,
        and this node writes no file of that name. `history.log` is
        `btclib_node`'s own, in `_chain_dir`, the directory the cookie
        file above is read from.
        """
        return self._chain_dir / "history.log"

    @override
    def _log_path(self) -> Path:
        """Return `log_path`, for `start`'s own `TimeoutError`."""
        return self.log_path
