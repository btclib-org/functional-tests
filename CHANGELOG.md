# Changelog

<!-- markdownlint-configure-file
  {
    // MD024/no-duplicate-heading - every release repeats the same few
    // headings, which is what keeps the page readable scrolling down it;
    // only a duplicate under the same release heading would be the
    // accident this rule looks for
    "MD024": { "siblings_only": true }
  }
-->

The history of this repository is `git log` of `main`. Nothing here is
released, so no entry is added. The entries below were written before
that, grouped by subject rather than by version, and stay as they are.

## v0 (work in progress, not released yet)

### The repository exists, with the organization's apparatus and the tf2 ledger

Step 2 of [ISS 2220](https://github.com/btclib-org/btclib/issues/2220): the
organization's toolchain, lint gate and tests apparatus, plus `TF2.md`
rebuilt with a census script for [ISS btclib-org/btclib#1751][1751].

[1751]: https://github.com/btclib-org/btclib/issues/1751

### A drift line names both commits whole

`check_vendored_vectors.py` prints the pinned commit and upstream's tip as
full shas, in its output and in the tracking issue, so two commits alike in
their first twelve characters print as two (issue btclib-org/.github#1343).

### The adapter: bitcoind first, btclib-node second

A `NodeAdapter` and a `Peer`, and `p2p_getdata` rewritten on both: passes
on bitcoind, fails on btclib-node (issue btclib-org/btclib-node#1072).
(closes #1)

### CONTRIBUTING.md and README.md link GOVERNANCE.md and ROADMAP.md

Both link `GOVERNANCE.md`, how the organization decides, and `ROADMAP.md`, what
it intends to do, one copy of each in btclib-org/.github (issue
btclib-org/.github#1359).

### The first family joins `p2p_getdata`, and a capability for `sendmsgtopeer`

`p2p_block_sync`, `p2p_compactblocks_hb`, `p2p_invalid_locator` and
`p2p_net_deadlock` join `p2p_getdata`, against bitcoind and btclib-node.
(closes #2)

### The disk family's mechanism, and `feature_blocksdir` ported

`NodeAdapter` gains `extra_args`; `Capability.BLK_FILES` names Core's
`blk*.dat` layout, absent from btclib-node by decision (issue
btclib-org/btclib-node#573). `feature_blocksdir` is ported (issue #7).

### The printed skip count sums the whole run, not the controller's own

Each `-n auto` worker hands its own tally to the controller through
`workeroutput`, and the controller folds every one in before printing
the total (closes #16).

### The log family: a wire half and a `Capability.DEBUG_LOG` half

`test_magic_bytes` and `P2PLeakTest`'s obsolete-version check are each
rewritten as a disconnect, passing on both nodes, and a log assertion,
`Capability.DEBUG_LOG`-gated and skipping on btclib-node. (issue #5)

### `NodeAdapter.start` carries a process's own stderr into its `RuntimeError`

Redirected to a file rather than an unread `subprocess.PIPE`, so a
long-running node cannot block on a full pipe buffer, and
`feature_blocksdir`'s refusal half matches each node's own wording (closes #19).

### The option family's mechanism, and `feature_uacomment` ported

`Capability.UA_COMMENT` names Core's `-uacomment`, declared by bitcoind
and absent from btclib-node's own registered flags. `feature_uacomment`
is ported (issue #3).

### The clock family's mechanism, and `rpc_uptime` ported

`Capability.CLOCK` names Core's `setmocktime`, absent from btclib-node.
`rpc_uptime` is ported: it is the one clock-family test needing the
clock alone; every other one also needs another mechanism (closes #6).

### `NodeAdapter` refuses an `extra_args` entry naming its own option

Reserved names are read out of `_command()` itself, so `-datadir`,
`-port`, `-rpcport` or `-regtest` -- any dash count, an `=value`, a `-no`
negation -- is refused rather than silently overriding it (closes #18).

### `BtclibNodeAdapter` authenticates a build that checks RPC credentials

A build gaining Core-style RPC authentication (btclib-node ISS #1070)
writes a cookie `BitcoindAdapter` already reads; the placeholder
credential this adapter carried is used only where a build has none (closes #25).

### A bitcoind-only test has a place of its own; `feature_torcontrol` is first

A `*_bitcoind_test.py` module with no `*_btclib_node_test.py` counterpart
needs no `Capability`; `TF2.md`'s per-test ledger spells such a row
`bitcoind only`. `feature_torcontrol` is the first (closes #23).

### `p2p_invalid_messages` gains more `Misbehaving` checks in the log family

Each is a wire disconnect and a `Capability.DEBUG_LOG`-gated log line;
btclib-node's own oversized-`inv` check fails (issue
btclib-org/btclib-node#1145). (issue #5)

### `feature_filelock` and `rpc_whitelist` join the disk family

`feature_filelock` passes on both nodes. `Capability.RPC_AUTH_CONFIG`
names `bitcoin.conf`'s RPC-auth keys, absent from the pinned
btclib-node build; `rpc_whitelist` is ported against it (issue #7).

### The option family's own census names its open candidates

`p2p_compactblocks_blocksonly` and `rpc_echo_payload` each need an
option alone; `TF2.md` names them, and why the rest of the re-measured
census is not a candidate (issue #3).

### The MiniWallet family's mechanism, and its first port

`MiniWallet` feeds its own coin cache from blocks it mines itself, never
`scantxoutset`. `feature_framework_miniwallet` is ported against
bitcoind, and skips on btclib-node for `Capability.MINE` (issue #4).

### `mempool_resurrect` and `mempool_spend_coinbase` join the MiniWallet family

`MiniWallet` gains `get_utxo`, a caller-named coin on
`create_self_transfer`/`send_self_transfer`, and `resync`; `build_fork`
builds a Core-style empty fork alongside it (issue #4).

### `p2p_invalid_messages` gains its size, drop and `addrv2` checks

`test_size` disconnects like `test_magic_bytes`; most other new checks
assert survival instead, and several rows fail on btclib-node, each
against its own already-filed issue, `TF2.md` naming which. (issue #5)

### README.md states the project's purpose, and how it is run

A non-regression suite for any node, beside or replacing Core's own
test framework; `CONTRIBUTING.md` gains the commands to run it
against your own checkout (issue btclib-org/btclib#2220).

### `rpc_users` completes the disk family

`-rpcauth`, `-rpccookieperms`, a malformed-`-rpcauth` roster, its own
interaction with named entries and `-norpcauth`, each against
`Capability.RPC_AUTH_CONFIG` or the new `RPC_AUTH_NEGATION` (closes #7).

### `core-master` and `btclib-node-main` are informational, not required

`core-master` builds `bitcoind` from Core's `master` and classifies its
own failures against `TF2.md`'s per-test ledger; `btclib-node-main`
installs from that project's `main` (issue #8).

### `NodeAdapter` waits for RPC by credential, not only by cookie

`NodeAdapter.__init__` gains an `rpc_auth` parameter, the credential a
caller already knows, so `start` waits on a node given `-rpcuser`/
`-rpcpassword` or `-norpccookiefile`, neither writing a cookie (closes #34).

### The softfork activation-height trio joins the option and MiniWallet families

`-testactivationheight` gains `Capability.TEST_ACTIVATION_HEIGHT`;
`feature_dersig`, `feature_cltv` and `feature_csv_activation` join the
option and MiniWallet families, `TF2.md` naming what each drops (issue #14).

### A fact that differs between builds is read from the build, for bitcoind too

`BitcoindAdapter` reads `Capability.MINE`'s own wallet dependency from the
running binary; `feature_torcontrol` and the newly ported
`p2p_bip434_feature` read the running build's own `getnetworkinfo` (closes #35).

### A Core developer runs the suite with the options `test_framework` gives them

`requires-python` moves to `3.12`. CONTRIBUTING.md maps `test_framework.py`
and `test_runner.py`'s options to pytest's, `--nocleanup`, `--tracerpc`,
`--timeout-factor` and `--v2transport` each gaining a real one (closes #36).

### The disk and MiniWallet families gain three single-mechanism leftovers

`feature_dirsymlinks` passes on both nodes; `feature_posix_fs_permissions`
fails on btclib-node (btclib-org/btclib-node#1198); `rpc_createmultisig` is
`bitcoind only`. (issue #4) (issue #6) (issue #7) (issue btclib-org/btclib#2220)

### Nodes connect to each other, disconnect, and wait for a mempool to agree

A mempool sync wait joins the block one, `rpc_setban` is ported in part,
a mixed bitcoind/btclib-node cluster gets a fixture, and `connect_nodes`
dials over v1 unless given `v2transport=True` (issue #43).

### The mempool-policy-option trio joins the option and MiniWallet families

`-datacarrier`, `-permitbaremultisig`, `-dustrelayfee` and `-bytespersigop`
each gain a `Capability`; `mempool_datacarrier`, `mempool_dust` and
`mempool_sigoplimit` join the option and MiniWallet families (issue #14).

### Bucket A's remaining files each need a mechanism, none of them the option's

`feature_prune_stale_fork`, `rpc_validateaddress`, `feature_nulldummy`,
`feature_versionbits_warning`, `rpc_signer` and
`feature_presegwit_node_upgrade` need a mechanism `TF2.md` names (issue #3).

### The `btclib-node` jobs gain the pinned `bitcoind`, and the mixed cluster runs

`btclib-node` and `btclib-node-main` install the same pinned release the
`bitcoind` job does and set `TF2_BITCOIND`, so the mixed cluster runs
there instead of skipping (closes #61).

### `feature_filelock`'s btclib-node test accepts either build's own wording

The released build's own uncaught RocksDB wording and the fixed build's
own clean "Cannot obtain a lock on directory" are both matched, rather
than only the first (closes #67).

### The mempool-cluster-option pair joins the option and MiniWallet families

`-limitclustercount` and `-limitclustersize` each gain a `Capability`;
`mempool_package_limits` and `mempool_updatefromblock` join them, and
`MiniWallet` gains multi-coin, chained and exactly-sized transfers (issue #14).

### `feature_filelock`'s bitcoind test pins which directory refused its lock

Each of its two tests matches Core's own whole refusal, the locked path
included, so a datadir refusal no longer passes the blocksdir test or the
reverse (closes #71).

### `NodeAdapter.stop` kills a node that ignores its termination

A node still running once the wait after `terminate` expires is killed
rather than left holding its datadir and ports, and a `TimeoutError`
carrying its stderr says so (closes #76).

### `test_addrv2_unrecognized_network` is ported, over `-debug=addrman`

`-debug=addrman` joins `-debug=net`, so the address-manager lines Core's own
`test_addrv2_unrecognized_network` asserts are in the log it reads, and
that test joins `p2p_invalid_messages`' `addrv2` checks (issue #5).

### A node whose start fails is killed, and every teardown stop is kept

`NodeAdapter.start` kills its process before raising, and the fixtures that
stop several nodes stop each one and chain every error rather than keeping
only one (closes #79).

### Transaction upload, UTXO hash, descriptor activity and block stats ported

`feature_utxo_set_hash`, `rpc_getdescriptoractivity` and `rpc_getblockstats`
join the MiniWallet family, each RPC with its own capability; `p2p_leak_tx`
joins it and the clock family, and `Peer` gains `sync_with_ping` (issue #14).

### `NodeAdapter.restart` takes replacement `extra_args` for one start

`restart([...])` uses them for that start alone, as Core's `restart_node`
does, and `rpc_setban`'s `-whitelist` and `-bantime` sections are ported on
it (closes #51).

### `feature_fastprune`, `p2p_eviction` and `rpc_scanblocks` are ported

Each gains a `Capability` for what it asks of a node, `btclib-node`'s adapter
declaring inbound eviction wherever its build has it; `tool_utxo_to_sqlite`
stays out, its subject a Core script rather than a node (issue #14).

### `MiniWallet` knows which of its coins a block holds

`get_utxo` and a new `get_utxos` take Core's `confirmed_only` and
`mark_as_spent`, an unnamed coin being the largest matured one as in Core, and
`resync` asks `gettxout` which coins a block mined elsewhere holds (closes #69).

### `feature_presegwit_node_upgrade` joins the option family

A lower `-testactivationheight=segwit@N` refuses to restart a chain mined
past it, in Core's own words, and `-reindex` upgrades it (issue #3).

### `NodeAdapter.stop` reports a crashed node, and `start` refuses a second

`stop` raises on a node that exited with a code other than 0, carrying its
stderr; a second `start` raises; `mine` loads its wallet after a restart or
over a reused datadir (closes #84) (closes #86) (closes #92).

### `tf2_master_verdict` can answer "candidate regression"

`classify` compared `TF2.md`'s abbreviated pin with the API's full sha, so
every master-only failure read as a stale port; the pin is now matched as a
prefix (closes #83).

### `MiniWallet`'s self-transfers take Core's fee, version and sequence

`create_self_transfer` and `send_self_transfer` take Core's `fee_rate`, `fee`,
`version`, `locktime` and `sequence` with its defaults, the multi pair the last
three; fees are in satoshis, and broadcasts pass `maxfeerate` 0 (closes #103).

### `mempool_dust`'s refusal cases assert the reason is dust

The below-threshold test asserts `testmempoolaccept`'s reject reason, not only
its `allowed` flag, as Core's own `mempool_dust.py` does at v31.1 (closes #93).

### `rpc_setban`'s restart check reads `listbanned`, then bitcoind's own log

`listbanned` is asserted right after `restart()`, and the refused dial is
read over `assert_debug_log` against bitcoind's own `dropped (banned)`, not
`connect_nodes`'s bare `TimeoutError` alone (closes #94).

### `capability.py` names its own hookwrapper, not an autouse fixture

`capability.py`'s module docstring names the mechanism that turns
`MissingCapabilityError` into a skip as `tests/conftest.py`'s own
`pytest_runtest_call` hookwrapper (closes #97).

### `nocleanup_bitcoind_test` stops its node on a failing assertion too

The datadir-survives-`stop` assertion now runs inside a `try`/`finally`, so
a failure no longer leaves the node running past the test (closes #95).

### `free_ports` reserves every port a fixture needs before any bind

`free_port`, called once per port, can hand two of them the same one; the
fixtures in `tests/integration/conftest.py` now draw theirs from `free_ports`,
which holds every probe open until all are bound (closes #85).

### `mempool_util.fill_mempool` pushes a node's mempool to eviction

Ported from Core's own `test_framework/mempool_util.py`, over a throwaway
`MiniWallet`: disposable, rising-fee self-transfers until `mempoolminfee`
rises above `minrelaytxfee` and the lowest fee-rate one is evicted (closes #70).

### Every integration module building its own adapter draws its ports from `free_ports`

Each drew its `rpc_port`/`p2p_port` pair from two separate `free_port()` calls,
the same collision `free_ports` exists to close; a module building several
adapters before starting any now draws all their ports at once (closes #115).

### The docs describe the tree's adapter as it stands, not as its first step

`CLAUDE.md`, `CONTRIBUTING.md`, `tests/README.md` and the package
docstring point at `CONTRIBUTING.md`'s public-surface list rather than
each naming a stale subset of it (closes #96).

### The killed-node test stops its own node on a failure before `stop`

The window between `start()` and this test's own first `stop()`, in both
twins, now runs inside a `try`/`finally` that kills the process only if it
is still alive (closes #114).

### A reference to another repository's issue is qualified with its owner and name

Bare `ISS 2220` in a comment or docstring resolved to this tracker,
where no such issue exists; citations of btclib and btclib-node now
carry the owner and repository, `TF2.md`'s own form (closes #100).

### `_wait_for_rpc` tells a node's own answer from silence

An `RpcError` other than `-28 RPC_IN_WARMUP`, or an `HttpError` carrying 401
or 403, fails at once with the node's own error as its cause, instead of
waiting out the startup timeout and reporting `TimeoutError` (closes #98).

### A shared, scaled `node.wait_until` replaces the unscaled test-local waits

Two modules' own polling loops ignored `--timeout-factor`; both now share
`node.wait_until`, and a `peer.receive`/`future.result` timeout in two
other modules is scaled the same way (closes #90).

### `tests/integration/conftest.py`'s hooks are thin calls into `tests/conftest.py`

`_stop_all` and the bodies of `pytest_sessionfinish` and
`pytest_testnodedown` now live in `tests/conftest.py`, outside
`[tool.coverage.run]`'s `omit`, and are unit-tested there (closes #101).

### A require-only btclib-node stub fails once its capability is declared

Every `*_btclib_node_test.py` test whose body was only `del`/`require(...)`
now ends in `pytest.fail`, and asks for the capabilities its bitcoind twin
does; `tests/require_only_stub_test.py` holds both to it (issue #82).

### Each `NodeAdapter` start redirects the node's stderr into a file of its own

Each start writes under the datadir's `stderr/`, as Core's `TestNode.start`
does, and `stop` reads its own start's file: a second adapter over one datadir
overwrote the one `node-stderr.log` a running node's `stop` read (closes #105).

### `rpc_getdescriptoractivity`'s payments each run on a node of their own

`test_activity_in_block` and `test_no_mempool_inclusion` pay 1 BTC from a
fresh node, as Core's own file does, not from the shared one, where a long
enough chain leaves every coinbase the test matures worth less (closes #127).

### `UA_COMMENT` and `V2TRANSPORT` measure both `btclib-node` builds

`cli.py`'s `_build_parser` is gone from `btclib-node`'s `main`, replaced by a
module-level `_OPTIONS` dict; the `UA_COMMENT` and `V2TRANSPORT` docstrings
now measure both the released build and `main` (closes #59).

### Every test runs under a per-test timeout

`pytest-timeout` joins `harness`, and `[tool.pytest.ini_options]`'s `timeout`
fails a test that hangs by name, scaled by `--timeout-factor` with the waits
beneath it (closes #91).

### A `NodeAdapter` start that times out says what the node was doing

Its `TimeoutError` names the failures waited out, as Core's
`wait_for_rpc_connection` does, then the start's stderr and what it appended to
bitcoind's `debug.log` or btclib-node's `history.log` (closes #134).

### `BtclibNodeAdapter` declares `RPC_AUTH_NEGATION` on a build reading `-norpcauth`

A probe asks the build's own `cli.build_config` whether `-norpcauth` discards
an `-rpcauth` given before it, so `rpc_users`'s negation test runs against
`btclib-node`'s `main` and skips against the release (closes #130).

### `BitcoindAdapter` passes `-rpcallowip`, so its `-rpcbind` takes effect

A taken `127.0.0.1` RPC port now fails bitcoind's init and `start` on the exit
code, where bitcoind ignored `-rpcbind` alone, served RPC on `::1` and left
`start` to time out (closes #135).

### `--tracerpc` traces every adapter a test builds

Test modules and `mixed_cluster` built their adapters without it; each now goes
through the `make_adapter` fixture, and `tests/tracerpc_reach_test.py` fails on
one that bypasses it without naming its own `trace_rpc` (closes #89).

### `rpc_getblockstats`'s `utxo_size_inc` expectations follow the running build

A coin is charged its serialized `TxOut` plus `PER_UTXO_OVERHEAD`, one byte
less from Core `v32.0.0` on (bitcoin/bitcoin#31449), read off
`getnetworkinfo`'s `version`, so Core's `master` passes too (closes #107).

### `feature_blocksdir` matches the whole refusal on both btclib-node builds

The refusal is matched as the node's whole stderr, the path included, in Core's
wording `main` writes or the released build's own, so `main` no longer fails it
and bitcoind's twin pins its text the same way (closes #141).

### `BtclibNodeAdapter` mines, on a build connecting a block with no peer

`mine` builds each block client-side and submits it; a probe of the build's own
`main.update_chain` declares `MINE` against `btclib-node`'s `main` and not the
release, where a solo node never connects a submitted block (closes #56).

### `tf2_master_verdict` names an unchanged Core file as measured

A master-only failure whose Core file has not moved since its pin reads "file
unchanged since the pin" where it read "candidate regression", naming no owner:
the port may never have matched that file, so it is checked first (closes #143).

### `BtclibNodeAdapter` declares `BAN` on a build serving a ban list

A probe of the build's own `rpc.callbacks.callbacks` declares `BAN` where it
names `setban`, `listbanned` and `clearbanned`, as `btclib-node`'s `main` does
and the release does not (closes #140).

### `tf2_master_verdict` reads the data files a Core test loads

A master-only failure reads "file unchanged since the pin" only where no data
file its Core test loads has moved either, where the entry closing #143 names
the test file alone; a moved data file is a stale port, named (closes #146).

### `check_vendored_vectors` does not read a removed pin as changed content

A pin whose file upstream deleted or renamed is reported as the commit that
removed it, which the report says may be a deletion or a rename; "no commit"
names a path the branch walked never held (closes #150).

### A `NodeAdapter` starts on the chain its caller names

`chain=` picks `main`, `test`, `testnet4`, `signet` or `regtest`, the default;
`BtclibNodeAdapter` refuses `testnet4`, and any chain but regtest starts with
`-connect=0`, drawing no peer and asking no seed (closes #63).

### `BitcoindAdapter`'s wallet probe is checked against a build tree's record

Only `MINE` rests on a `test/config.ini` component, `ENABLE_WALLET`; the adapter
still probes the binary, and a test holds `MINE` to that file in a build tree
and on for a release, which guix builds with the wallet (closes #53).

### The `btclib-node` jobs fail when their tests never reached a node

Each fails on an empty report or on a skip that is not a capability's: an
`import btclib_node` raising after a good install skips every test, and pytest
alone exits 0 on that (closes #87).

### `btclib-node` keeps its own interpreter rather than a dependency group

`pyproject.toml`'s `[dependency-groups]` comment says why: btclib-node's floor
sits above the interpreter `.python-version` names, and `TF2_BTCLIB_NODE_PYTHON`
names any build of it -- a release, its `main`, a checkout (closes #42).

### `rpc_users.py`'s btclib-node test accepts Core's malformed `-rpcauth` wording

A malformed `-rpcauth` matches either whole stderr: Core's "Unable to start HTTP
server", which btclib-node's `main` writes, or the older "Invalid -rpcauth
argument." (closes #159).

### `REVIEWING.md` lets a filed issue carry its fix

An issue filed from a review may say the fix where one is known: *What is
filed, and what is not* dropped its "no fix", the filing bar standing as it
was (issue btclib-org/.github#1378).

### The `btclib-node` jobs report what moved against `TF2.md`

Each compares its report with the ledger's `btclib-node` column, reading the
verdict a cell gives its own build: a known failure no longer colours the job,
and a row that moved, one now passing included, does (closes #88).

### `_negates_rpcauth` reads `rpc_auth_invalid`, not a bare return

Since btclib-node keeps a malformed `-rpcauth` there rather than raising
(issue btclib-org/btclib-node#1210), a bare return no longer says
`-norpcauth` discarded it (closes #160).

### `TF2.md`'s btclib-node fail cells carry a build qualifier

A fail cell claimed every build though btclib-node's `main` already passes
it; it now carries the skip cells' own build-qualified form, and the prose
explaining it, ledger and tests alike, names the released build (closes #157).

### `feature_nulldummy` is ported, its multisig spends built by the test

Every step of Core's own file runs, each spending a multisig that requires no
signature, NULLDUMMY reading the dummy whatever the required count; the test
builds each scriptSig and witness, and `MiniWallet` gains nothing (closes #64).

### `p2p_disconnect_ban`'s `disconnectnode` half is ported

It passes on bitcoind and skips on btclib-node, which has no `disconnectnode`
RPC. `TF2.md` hands every other node-linking file to ISS 14, this file's
`setban` half among them, unless the census disqualifies it (closes #43).

### One body runs a Core test against both nodes

`p2p_block_sync`, `p2p_compactblocks_hb` and `p2p_invalid_locator` call one
body from both nodes' tests, so a btclib-node build declaring the capabilities
runs Core's scenario rather than a stub's `pytest.fail` (issue #125).

### `feature_nulldummy`'s multisig spends are signed, as Core's own are

Each spends a 1-of-1 multisig the test signs with btclib, so a node reads the
dummy beneath a signature; a tampered dummy leaves the signature valid, and
NULLDUMMY is still the only refusal (closes #165).

### `rpc_validateaddress` is ported, with Core's address tables

On bitcoind started on the main chain, each of Core's invalid addresses answers
its own error and `error_locations`, and each valid one its own `scriptPubKey`;
btclib-node's test skips on `Capability.VALIDATE_ADDRESS` (closes #153).

### `feature_dersig`'s non-DER signature is ported, signed with btclib

`mini_wallet.py` gains Core's own `RAW_P2PK` output and its signature, and
`feature_dersig`'s spend of it is mined before BIP66 and refused after, by the
mempool and a block alike (issue #167).

### Two MiniWallet mempool tests and `feature_utxo_set_hash` run one body

`mempool_resurrect`, `mempool_spend_coinbase` and `feature_utxo_set_hash` call
one body from both nodes' tests, each reading a block's transactions off the
raw `getblock` both nodes serve (issue #125).

### `rpc_setban` runs one body per test on both nodes

Each of its tests calls one body from both nodes' tests, so a btclib-node build
serving the ban list runs Core's scenario rather than a stub's `pytest.fail`
(issue #125).

### A node can be made to dial the test, and `p2p_addrfetch` is ported

`peer.Listener` accepts a node's own outbound connection as a `Peer`, and
`NodeAdapter.add_outbound_connection` asks for one of a chosen type under
`Capability.TYPED_OUTBOUND`, which btclib-node declares on no build (issue #44).

### `rpc_getdescriptoractivity`'s remaining subtests are ported

Several addresses at once, a confirmed payment beside an unconfirmed one, a
receive and its spend over two blocks, and a `RAW_P2PK` script with no address,
the last spent under `mini_wallet.py`'s signing helper (issue #167).

### `rpc_echo_payload` is ported, with `Capability.RPC_WORK_QUEUE`

Under Core's `-rpcworkqueue=2` and `-rpcthreads=2`, bitcoind answers every
concurrent `echo` and `sendrawtransaction`, or refuses it with HTTP 503, never
timing out; btclib-node skips on `Capability.RPC_WORK_QUEUE` (issue #3).

### `p2p_eviction` runs one body on both nodes

Both nodes' tests call one body, which asks the node's `-help` whether it splits
its inbound slots by relay, so a btclib-node build that evicts and mines runs
Core's scenario rather than a stub's `pytest.fail` (issue #125).

### The log family's census is recorded, and more of its checks ported

`p2p_handshake`'s redundant `verack`, `p2p_addr_relay`'s oversized `addr` and
`p2p_addrv2_relay`'s late `sendaddrv2` run on both nodes; `TF2.md` names where
every other Core test reading the log goes (issue #5).

### The `-blocksonly`, `getblockfilter` and `getblockfrompeer` tests are ported

`p2p_compactblocks_blocksonly`, `rpc_getblockfilter` and `rpc_getblockfrompeer`
skip on btclib-node; `Peer` gains Core's `last_message` and a `handshake`
offering chosen services (issue #3).

### `feature_cltv`'s failure reasons are ported, with `Capability.ACCEPT_NON_STANDARD`

BIP65's failure reasons are mined before activation, then refused by
`testmempoolaccept` under Core's `-acceptnonstdtxn` and by `submitblock`; one
body runs on both nodes (issue #167).

### `feature_framework_miniwallet` runs one body per test on both nodes

Each test calls one body over either node, so a btclib-node build that mines
runs it rather than a stub's `pytest.fail`; `confirmed_only`, `fee_rate` and
TRUC have `TF2.md` rows of their own (issue #125).

### `mini_wallet.build_next_block` builds one block on the node's tip

It returns a solved, unsubmitted block paying a chosen coinbase `scriptPubKey`,
carrying chosen transactions at a chosen header version; the integration tests
that kept their own copy of the builder call it instead (closes #178).

### `p2p_node_network_limited` is ported, and the option family's census recorded

`p2p_node_network_limited` runs on bitcoind and skips on btclib-node, and
`setnetworkactive` is `Capability.SUSPEND_NETWORK`. `TF2.md` records the option
family's census and where each file it lists goes (closes #3, closes #185).

### `rpc_getdescriptorinfo` is ported, with Core's descriptor table

bitcoind answers Core's descriptors with their checksums, flags and multipath
expansions, and refuses each malformed request with Core's own error;
btclib-node skips on `Capability.DESCRIPTOR_INFO` (closes #174).

### `p2p_timeouts`, `p2p_ping` and `mempool_expiry` are ported

Each runs one body on both nodes under Core's own `-peertimeout` or
`-mempoolexpiry`, the node's clock moved by `setmocktime`; btclib-node skips on
the new `Capability.PEER_TIMEOUT` and `Capability.MEMPOOL_EXPIRY` (issue #14).

### `node-integration.yml` installs bitcoind with `btclib-org/.github`'s script

The `btclib-node` jobs use it, and the `install-bitcoind` action goes (issue
btclib-org/.github#1373); the `bitcoind` job exempts btclib-node's absence by
its skip reason, not its module name (issue btclib-org/.github#1377).

### `test: every job passed` accepts its own `needs` job listed unfinished

A `needs` job the jobs listing still shows unfinished is accepted where its own
result is `success` or `skipped`; any other unfinished job is still refused
(issue btclib-org/.github#1395).

### `REPOSITORY.md` reads back classic signatures off and SHA pinning on

Classic `required_signatures` reads `false`, and `allowed_actions` and
`sha_pinning_required` are read back, section 11 having the reasons (issue
btclib-org/.github#1409).

### `rpc_createmultisig`'s spend half is ported

bitcoind signs each multisig spend in two parts, merges them and mines it, and
`createmultisig`'s key-count limits and legacy fallback are checked;
btclib-node skips on `Capability.SIGN_RAW_TRANSACTION` (issue #167).

### `feature_dersig`, `rpc_uptime` and option tests run one body on both nodes

`feature_uacomment`, `rpc_uptime`, `v2transport_option`, `rpc_echo_payload`,
`feature_fastprune`, `mempool_fill` and `feature_dersig` call one body per test,
which asks the running node rather than its class for capabilities (issue #125).

### `feature_csv_activation`'s body is ported, with `Capability.INVALIDATE_BLOCK`

BIP68, BIP112 and BIP113 are checked block by block around Core's own
activation height; one body runs on both nodes, and
`mini_wallet.build_next_block` takes the time its block carries (closes #167).

### `feature_nulldummy` and mempool option tests run one body on both nodes

`feature_nulldummy`, `feature_presegwit_node_upgrade`, `mempool_datacarrier`,
`mempool_dust`, `mempool_package_limits`, `mempool_sigoplimit` and
`mempool_updatefromblock` call one body per test, over either node (issue #125).

### `p2p_add_connections` and the rest of `p2p_handshake` are ported

bitcoind dials typed outbound peers and drops a feeler, a peer short of the
services its type expects and a dial to itself; `manual` is asserted per build.
btclib-node skips on `Capability.TYPED_OUTBOUND` (issue #44).

### `feature_includeconf` and `feature_reindex_init` are ported

btclib-node runs `feature_includeconf`'s refusals and warning, skipping the
include order on `Capability.UA_COMMENT`, and skips `feature_reindex_init` on
the new `Capability.REINDEX_AFTER_FAILURE` (issue #14).

### `rpc_generate`, `rpc_signrawtransactionwithkey` and `rpc_scantxoutset` are ported

bitcoind mines blocks to a given address or descriptor, signs with given keys
and searches its UTXO set by descriptor; `Capability.GENERATE` and
`Capability.SCAN_UTXO_SET` are new, and btclib-node skips each (issue #4).

### `test: every job passed` reads a lagging row again and judges `needs` results

A `needs` row still listed unfinished is read again, up to three times ten
seconds apart, before being accepted (issue btclib-org/.github#1416); a `needs`
result neither `success` nor `skipped` fails (issue btclib-org/.github#1424).

### More p2p tests run one body on both nodes

`p2p_disconnect_ban`, `p2p_invalid_messages` but its `addrv2` checks,
`p2p_leak`, `p2p_leak_tx` and `p2p_net_deadlock` call one body per test, over
either node (issue #125).

### `feature_proxy` is ported in part, on a mock SOCKS5 proxy

`socks5.Socks5Proxy` records what a node asks a SOCKS5 proxy for, and bitcoind
routes each network through the proxy `-proxy` or `-onion` gives it.
btclib-node skips on `Capability.PROXY` (issue #47).

### The first node-wallet tests are ported, with `Capability.NODE_WALLET`

`wallet_signmessagewithaddress`, `wallet_blank` and `wallet_coinbase_category`
run against bitcoind's own wallet, reached at `/wallet/<name>`; btclib-node
skips on `Capability.NODE_WALLET` (issue #45).

### `p2p_initial_headers_sync` and `p2p_sendtxrcncl` are ported

The headers timeout drops a stalling sync peer and spares a `noban` one; BIP330's
`sendtxrcncl` is checked sent, withheld, ignored and refused, gated on
`Capability.TX_RECONCILIATION` and `Capability.PEER_BLOOM_FILTERS` (issue #44).

### RPC stubs and `feature_blocksdir` run one body; its refusal is Core's alone

`feature_blocksdir`, `rpc_validateaddress`, `rpc_scanblocks`,
`rpc_getblockstats`, `rpc_getdescriptorinfo` and `rpc_getdescriptoractivity` run
one body per test; the released btclib-node fails the refusal (issue #125).

### The bodies calling `generateblock` ask for `Capability.GENERATE`

`feature_framework_miniwallet`'s `confirmed_only` and a
`mempool_updatefromblock` test skip where a node lacks it: btclib-node's
`main` skips `confirmed_only` rather than failing it (closes #207).

### `feature_reindex` and `feature_reindex_readonly` are ported

bitcoind reindexes back to its height, over blocks out of order, after a
stopped reindex and from a read-only block file, and logs Core's `reindex`
category; btclib-node skips on the new `Capability.REINDEX` (issue #14).

### `TF2.md`'s `rpc_setban` and `p2p_eviction` cells name btclib-node's fixes

`rpc_setban.py`'s non-IP row passes past btclib-org/btclib-node#1218;
`p2p_eviction.py`'s fails on some runs past btclib-org/btclib-node#1179, a
`ping` overtaking the `tx` before it (btclib-org/btclib-node#1410) (issue #212).

### `feature_filelock` and `rpc_users` match a refusal in Core's wording alone

btclib-node's lock refusal and malformed `-rpcauth` refusal are each matched in
Core's wording, as `feature_blocksdir`'s is: the released btclib-node fails
`feature_filelock` (closes #211).

### `p2p_feefilter` is ported

BIP133's `feefilter` is checked sent to an inbound peer and withheld from a
`forcerelay`, a block-relay-only and a `-blocksonly` one, and a peer's own
filter is checked to withhold announcements below it until lifted (issue #44).

### `Peer.sync_with_ping` is a barrier on a node processing messages in order

Its docstring promises the barrier only on a node processing each peer's
messages in order, as Core's does, and not on one answering a `ping` ahead
of them (btclib-org/btclib-node#1410) (issue #212).

### `p2p_message_capture` and `feature_blocksxor` are ported

bitcoind captures a peer's messages under `-capturemessages` and refuses
`-blocksxor=0` over a stored key; btclib-node skips on the new
`Capability.CAPTURE_MESSAGES` and `Capability.BLOCKS_XOR` (issue #14).

### More node-wallet tests are ported, and `wallet_disable` on bitcoind alone

`wallet_createwalletdescriptor`, `wallet_sendmany` and `wallet_timelock` run
against bitcoind's own wallet, btclib-node skipping each on `NODE_WALLET`;
`wallet_disable` runs against bitcoind alone (issue #45).

### `feature_proxy`'s remaining nodes and refused starts are ported

`socks5.Socks5Proxy` also listens on IPv6 loopback or a unix socket. bitcoind is
checked through either, and under `-cjdnsreachable`, `-i2psam` or a per-network
`-proxy`, and on every start Core refuses; btclib-node skips (issue #47).

### `mempool_accept_wtxid` and `rpc_orphans` are ported

bitcoind tells two children sharing a txid apart by wtxid, and keeps a child
sent ahead of its parent as an orphan. btclib-node skips `rpc_orphans` on the
new `Capability.ORPHANAGE`; its `main` fails `mempool_accept_wtxid` (issue #4).

### `wallet_simulaterawtx` and `wallet_rescan_unconfirmed` are ported

Both run against bitcoind's own wallet, btclib-node skipping each on
`NODE_WALLET` (issue #45).

### bitcoind runs without SQLite wallet syncs, as under Core's own harness

bitcoind starts with `-unsafesqlitesync`, as Core's harness starts it, so mining
over a loaded wallet stays within the RPC client's timeout under load; an
`extra_args` naming it is refused (closes #226) (closes #201) (closes #225).

### `p2p_mutated_blocks` is ported

A peer sending a mutated block is checked dropped without clearing an honest
peer's request for the block, as is one sending a block of unknown parent
(issue #44).

### `mining_template_verification` is ported

bitcoind checks a block proposed over `getblocktemplate` without storing it,
refusing each malformed one in Core's own words; btclib-node skips on the new
`Capability.BLOCK_PROPOSAL` (closes #4).

### `--timeout-factor` scales the adapters' RPC client timeout, as Core's does

Both adapters' RPC clients bound a call by the new
`timeout_factor.rpc_client_timeout`, Core's own `int(60 * factor) // 2`: 30 s at
the default factor, as before, and 90 s at `--timeout-factor 3` (closes #230).

### `feature_includeconf`'s double-negative row passes on btclib-node's `main`

btclib-node's `main` passes it, being past btclib-org/btclib-node#1402; a build
past btclib-org/btclib-node#1409 and before it still fails, warning about the
double negative on stderr ahead of the refusal (closes #231).

### `feature_remove_pruned_files_on_startup` is ported

bitcoind deletes pruned block files a reader still holds open, and a
`-reindex` under `-prune` wipes the rest; btclib-node skips on
`Capability.FASTPRUNE` (issue #14).

### bitcoind logs Core's `validation` category

bitcoind starts with `-debug=validation`, so `p2p_mutated_blocks` asserts the
`Block mutated` line Core's own test asserts (issue #44).

### `p2p_i2p_ports` and `p2p_i2p_sessions` are ported

A node given `-i2psam` is checked to dial I2P on port 0 alone, over the SAM
session `-i2pacceptincoming` picks; bitcoind runs with `-debug=i2p`, and
`assert_debug_log` given `timeout=0` reads the log once, as Core's (issue #47).

### `_command`'s docstring ties `-debug` to the `LogDebug` lines alone

A `-debug` category gates only the log family's `LogDebug` lines; the rest,
`LogInfo`'s `Reindexing finished` for one, print regardless (closes #240).

### `wallet_anchor` and the decaying miniscript multisig test are ported

`wallet_anchor` and `wallet_miniscript_decaying_multisig_descriptor_psbt` run
against bitcoind's own wallet, btclib-node skipping each on `NODE_WALLET`
(issue #45).

### A btclib-node module's run command pastes as one command

A docstring continuing the `uv run pytest` line does so with one backslash,
which a shell reads as a continuation rather than as an escaped backslash
(closes #236).

### `MiniWallet` caches a transaction when it sends it, not when it builds it

A `create_*` method caches no coin it creates, nor a refused send, as Core's
`scan_tx` runs on send; `MiniWallet.new_utxos` answers a transaction's coins
uncached, for a caller spending a created transaction's output (closes #242).

### `p2p_dns_seeds` is ported

A node is checked to query its DNS seeds when Core's does, `Capability.DNS_SEED`
naming `-dnsseed` and `-forcednsseed`, and `Capability.KNOWN_ADDRESSES`
`addpeeraddress` and `getnodeaddresses`; btclib-node skips (issue #47).

### `feature_anchors` and `p2p_addr_selfannouncement` are ported

bitcoind's anchors and `-externalip` self-announcements are checked, the latter
over `Peer.handshake`'s new `addrv2`; btclib-node skips on `TYPED_OUTBOUND` and
the new `Capability.EXTERNAL_IP` (issue #44).

### `Socks5Proxy` releases what it acquired when its construction fails

A `Socks5Proxy` whose `bind`, `listen` or serving thread fails closes its socket
and removes a unix socket's directory before raising (closes #253).

### `MiniWallet.generate` caches a confirmed coin its wallet did not send

A `confirm` transaction no send of this wallet scanned is scanned when mined,
as Core's `generate` finds its coins through `rescan_utxos`; a coin a mempool
transaction already spends is left out (closes #247).

### `Listener` closes its socket when its timeout is refused

A `Listener` whose timeout `settimeout` refuses closes its socket before raising
(closes #254).

### `p2p_seednode` is ported

A node is checked to ask its `-seednode` when Core's does, under
`Capability.ADDRESS_FETCH`; `assert_debug_log` takes `unexpected_substrings`,
Core's own `unexpected_msgs`; btclib-node skips (issue #47).

### `--timeout-factor 0` disables the waits, as Core's does

`--timeout-factor 0` scales every wait by 999, Core's own reading of 0, where it
made the adapters' RPC client timeout 0 and no node could start (closes #234).

### `Peer.is_connected` answers without waiting

`Peer.is_connected` is Core's `P2PInterface.is_connected`, and `p2p_eviction`
reads it; `rpc_setban`'s restart check dials with Core's `addnode ... "onetry"`.
Neither waits for a timeout `--timeout-factor` scales to run out (closes #250).

### `p2p_ibd_stalling` is ported

A node is checked to drop a peer stalling initial block download, doubling the
timeout for the next, and to pause a stalling `manual` peer instead; btclib-node
skips (issue #44).

### `p2p_ibd_stalling` fails a peer dropped mid-sync

A peer the node closes during a ping round trip fails the check, as it fails
Core's, where its `ConnectionError` was suppressed (closes #262).

### `p2p_private_broadcast` is ported

`Socks5Proxy` forwards each connection where a `destinations_factory` names;
a node is checked to broadcast as `-privatebroadcast` does, its log category
added only to a node given that option, and btclib-node skips (issue #47).

### `p2p_tx_download` is ported

A node is checked to ask one announcing peer at a time for a transaction, an
outbound or `noban` one first; `Peer.handshake`'s new `wtxidrelay` off is Core's
`P2PInterface(wtxidrelay=False)`. btclib-node skips most checks (issue #44).

### `p2p_blocksonly` is ported

A `-blocksonly` node is checked to drop a peer sending it a transaction, bar one
holding the `relay` permission, and a node to drop a block-relay-only peer doing
so; btclib-node skips (issue #44).

### `p2p_orphan_handling` is ported in part

A node is checked to ask the peers announcing an orphan for its missing parents,
an outbound one first, and to take the orphan in once they arrive; bitcoind logs
Core's `mempoolrej` category. btclib-node skips (issue #44).

### `p2p_compactblocks` is ported in part

A node is checked to announce, build, serve and reconstruct BIP152 compact
blocks for a peer asking for them, and to ask only for the transactions it
lacks; btclib-node, on a build that mines, fails every check (issue #44).

### `p2p_compactblocks`' checks of malformed and invalid messages are ported

A node is checked to drop a peer sending a malformed or invalid compact block
message, not one whose block fails validation; bitcoind before `v32.0rc1` and
btclib-node keep the peer for two of them (issue #44).

### `p2p_compactblocks` is ported

A node is checked to reconstruct a compact block past a stalling peer, keep a
slot for an outbound one, report high-bandwidth peers, and ignore an unasked
compact block from any other; bitcoind before `v32.0rc1` takes it (issue #44).

### `[tool.uv] required-version` follows the `uv` `dependabot-core` bundles to `0.12.19`

`required-version` reads `>=0.12.19`: a floor below the `uv` pin
`dependabot-core` bundles admits a `uv` older than the one Dependabot writes
`uv.lock` with (issue btclib-org/.github#1438).

### `check_vendored_vectors` reads a bare `behind` line

A `behind` key with no value after it, the shape `trailing-whitespace` gives an
empty `behind` line, is reported as a line present but empty where it read as no
`behind` line at all (closes #184).

### `feature_startupnotify` is ported

bitcoind runs the `-startupnotify` command once per start; btclib-node skips
on the new `Capability.STARTUP_NOTIFY` (issue #14).

### `p2p_eviction` waits for a transaction in the mempool, not for a `pong`

Each transaction peer waits until `getrawmempool` lists what it sent, so a
`ping` answered ahead of the `tx` (btclib-org/btclib-node#1410) no longer fails
`p2p_eviction.py` on btclib-node past btclib-org/btclib-node#1179 (closes #212).

### `feature_torcontrol`'s mock Tor control server waits past its first second

The mock server keeps listening when no connection arrives within a second of
its start, so a `bitcoind` that reaches `-torcontrol` later than that is answered
rather than left unread until the test's own timeout (closes #198).

### `p2p_tx_download`'s racy rows read fail on btclib-node `main`

The disconnect and `notfound` rows read fail on btclib-org/btclib-node#1196,
whose race with Core's one-peer-asked read CI's runners usually lose, so the
`btclib-node` `main` job stops reporting both as regressions (closes #274).

### `free_ports` draws below the kernel's ephemeral range, one block per worker

Below the range Linux and macOS pick local ports from by default, one block per
numbered `pytest-xdist -n` worker, so neither an outgoing connection nor another
numbered worker started with it takes one before the node binds it (closes #192).

### `rpc_dumptxoutset` is ported

bitcoind's UTXO set dump is checked against what the same node reports rather
than against Core's constant hashes, a dump at a forked height per build;
btclib-node skips on the new `Capability.DUMP_UTXO_SET` (issue #14).

### `feature_loadblock` is ported

bitcoind imports a bootstrap file the test writes in `linearize-data.py`'s
format under `-loadblock`; btclib-node skips on the new `Capability.LOAD_BLOCK`
(issue #14).

### `p2p_orphan_handling`'s checks needing no-witness coins are ported

A node is checked to refuse the descendants of a parent refused for its size, to
ask for no parent it keeps as an orphan or already asks a peer for, and to ask
again for one refused for its fee or witness; btclib-node skips (issue #44).

### `feature_port` is ported

bitcoind listens where `-port` and `-bind` say, read off its `Bound to` lines,
and refuses a `-port` of `65536` or `0`; btclib-node skips on the new
`Capability.LISTEN_ADDRESS` (issue #14).

### An adapter keeps its RPC connections open across calls

Each thread calling a node keeps one connection to it until `stop`, rather than
one per call, each of which left a socket in `TIME_WAIT` on the node's port
(closes #278).

### `test_stop_terminates_without_waiting_on_a_call_in_flight` is deterministic

The test server holds the in-flight call for twice the client's timeout rather
than exactly as long, so a `stop` closing the connections before terminating the
node fails the test every time instead of as a rule (closes #286).

### `p2p_orphan_handling` is fully ported

A node is checked to ask for a parent confirmed ahead of a block taken off its
tip, and to keep an ancestor package of the largest size as an orphan while
other peers fill its orphanage; btclib-node skips (issue #44).

### `p2p_opportunistic_1p1c` is ported in part

A node is checked to take a parent refused for its fee into the mempool with a
child paying for it, the pair sent by one peer, and to ask for no parent of a
child whose parents are each refused; btclib-node skips (issue #44).

### `p2p_opportunistic_1p1c` is fully ported

A node is checked to take a parent and the child it keeps as an orphan in
together while other peers send it large orphans, or many small ones;
btclib-node skips (issue #44).

### `p2p_block_times` and `p2p_outbound_eviction` are ported

A node is checked to record when a peer first announces a block extending its
tip, and to drop an outbound peer that does not catch up with its tip in time
unless it is protected; btclib-node skips (issue #44).

### `p2p_tx_privacy` is ported

A node is checked to announce a transaction only to a peer whose handshake was
complete when the node received it; btclib-node's release skips (issue #14).

### `p2p_invalid_block` is ported

A node is checked to refuse a block with a duplicated transaction, an input
spent twice, an overpaying coinbase or a time too far ahead of its clock, and
to take each valid block; btclib-node skips (issue #14).

### `feature_maxtipage` is ported

A node restarted with `-maxtipage` is checked to leave initial block download
at the first tip exactly that old; btclib-node skips on the new
`Capability.MAX_TIP_AGE` (issue #14).

### `p2p_blockfilters` is ported

A node under `-peerblockfilters` is checked to serve BIP157 filters for its
active and stale blocks and to drop each invalid request; btclib-node skips on
the new `Capability.PEER_BLOCK_FILTERS` (issue #14).

### `p2p_getaddr_caching` is ported

A node listening on a loopback and two onion binds is checked to answer every
`getaddr` through one bind with the same addresses, through each a different
answer, and a new one once the cache expires; btclib-node skips (issue #14).

### `mempool_reorg` is ported

A reorg is checked to return coinbase spends to the mempool or drop them as
immature or non-final, and a transaction it returns to be served before it is
announced; btclib-node skips (issue #14).

### `interface_rpc` is ported

Raw JSON-RPC batches and statuses, notifications, `getrpcinfo` and a full work
queue's 503 are checked; btclib-node skips the last three, and its release
fails the first two (btclib-org/btclib-node#1109) (issue #14).

### `p2p_ibd_txrelay` is ported

A node in initial block download is checked to send the top `feefilter` and
ignore transactions and their announcements, and not once out of it;
btclib-node skips, and `Capability.MIN_RELAY_TX_FEE` is new (issue #14).

### `CONTRIBUTING.md` points a newcomer at `good first issue`

An issue carrying the label is small and self-contained: *The issue
tracker* says so, and links the organization-wide search for the open
ones (issue btclib-org/.github#1362).

### `feature_bip68_sequence` is ported

The mempool is checked to hold spends to BIP68's relative lock times, across a
reorg too, where a block before the deployment activates is not held to them;
btclib-node skips (issue #14).

### `mempool_accept` is ported

`testmempoolaccept` is checked to give each of Core's raw transactions its
verdict, reject reason and fees, the newer vsizes read per build; btclib-node
skips on `-permitbaremultisig` (issue #14).

### `mempool_cluster` is ported

A cluster is checked to be reported in chunks, and a transaction taking it past
`-limitclustercount` or `-limitclustersize` to be refused; btclib-node skips,
and `Capability.CLUSTER_LINEARIZATION` is new (issue #14).

### `feature_minchainwork` is ported

A node under `-minimumchainwork` is checked to relay no block, and to answer
`getheaders` with no header, until its chain has the work the option names;
btclib-node skips, and `Capability.MINIMUM_CHAIN_WORK` is new (issue #14).

### `mempool_packages` is ported

A mempool transaction's ancestors and descendants, and the transaction spending
each output, are checked, a prioritisation and a reorg included; btclib-node
skips, and `Capability.MEMPOOL_GRAPH` is new (issue #14).

### `feature_versionbits_warning` is ported

A node is checked to warn, and to run its `-alertnotify` command, once blocks
signalling an unknown version bit activate unknown rules; btclib-node skips on
the new `Capability.ALERT_NOTIFY` (issue #14).

### `mempool_package_rbf` is ported

A package replacing its parent's conflicts is checked, and each refusal to carry
Core's reason; btclib-node skips. `Capability.PACKAGE_ACCEPTANCE` is new, and
every ported test handing a node a package over RPC now asks for it (issue #14).

### `p2p_headers_sync_with_minchainwork` is ported

Nodes are checked to ignore a chain with less work than `-minimumchainwork`
until it has it, the headers of a `noban` peer still taken, and to report a
peer's low-work headers as presynced; btclib-node skips (issue #14).

### `p2p_unrequested_blocks` is ported

An unrequested block is checked to be stored only with at least the tip's work,
its header not at all below `-minimumchainwork`; an invalid block, or one whose
parent is unknown, drops the peer; btclib-node skips (issue #14).

### `mempool_cluster` asks for `Capability.MEMPOOL_GRAPH`

`mempool_cluster` checks a chain's ancestors and descendants, so it asks for the
capability serving them too, and skips on a node not declaring it (issue #14).

### `p2p_1p1c_network` is ported

Packages submitted to one node are checked to reach a line of nodes over p2p,
each parent paying no fee relayed with its child, though some nodes already kept
the child as an orphan or refused the parent; btclib-node skips (issue #14).

### `mempool_ephemeral_dust` is ported

A zero-fee parent with one dust output is checked to enter only with a child
spending that dust, and a reorg to return it with the dust unspent; btclib-node
skips, but for a parent tested alone under `-minrelaytxfee=0` (issue #14).

### `feature_notifications` is ported

A node's `-blocknotify`, `-alertnotify` and `-shutdownnotify` commands are
checked to run, `-walletnotify` left unported; btclib-node skips, and
`Capability.BLOCK_NOTIFY` and `Capability.SHUTDOWN_NOTIFY` are new (issue #14).

### `feature_settings` is ported

A node's `settings.json` is checked to be written, logged and kept, refused when
malformed or with a non-string `wallet`, and set aside by `-nosettings` or
`-settings`; `Capability.SETTINGS_FILE` is new; btclib-node skips (issue #14).

### `rpc_getchaintips` is ported

`getchaintips` is checked to report the active tip, a `valid-fork` once a split
network rejoins, and a `headers-only` chain turning `invalid`; btclib-node skips
(issue #317).

### `rpc_preciousblock` is ported

`preciousblock` is checked to switch a node between tips of equal work and back,
and to leave it on a longer one; `Capability.PRECIOUS_BLOCK` is new; btclib-node
skips (issue #317).

### `rpc_invalidateblock` is ported

`invalidateblock` and `reconsiderblock` are checked to move a node off a chain
and back, ancestors and descendants included; `Capability.INVALIDATE_BLOCK`
names both; btclib-node skips (issue #317).

### `rpc_signmessagewithprivkey` is ported

`signmessagewithprivkey` and `verifymessage` are checked against Core's own key,
signature and refusals; `Capability.SIGN_MESSAGE_WITH_PRIVKEY` is new;
btclib-node skips (issue #317).

### `feature_chain_tiebreaks` is ported

A node is checked to take, of tips with equal work, the one whose chain it held
in full first, and to keep it across a restart; btclib-node skips (issue #317).

### `p2p_sendheaders` is ported

A node is checked to announce blocks to a peer by `inv` until `sendheaders` and
by their headers after it, and to fetch announced headers; btclib-node skips
(issue #317).

### `feature_shutdown` is recorded as blocked

`TF2.md` names `feature_shutdown.py` as not ported until `NodeAdapter` can wait
for a node that exits on its own (issue #318) (issue #317).

### `p2p_fingerprint` is ported

A node is checked to withhold from a peer a block off its active chain, and its
header, once the block is a month or more older than its best header;
btclib-node skips (issue #317).

### `rpc_estimatefee` is ported

`estimatesmartfee` and `estimaterawfee` are checked against Core's own refusals
and valid calls; `Capability.ESTIMATE_SMART_FEE` is new; btclib-node skips
(closes #317).

### bitcoind runs with Core's own `-rpcservertimeout=99000`

bitcoind closes an idle RPC connection after 99000 s, not 30 s, a close a reused
connection could report as `ConnectionResetError`; a thread's kept connection
closes with the thread, freeing a `-rpcmaxconnections` slot (closes #336).

### A `Peer` read waits its own timeout, not the last one's

`Peer.receive`'s `timeout` holds for that call alone, so a later `receive`
without one, and `send`, wait the peer's default rather than what an earlier
`receive` or `wait_for` left on the socket (closes #326).

### `tests/socks5_test.py` reads a reply whole on Linux

The client of `tests/socks5_test.py` loops on `recv` until it has every octet it
asked for, where `MSG_WAITALL` on a socket with a timeout returned only what
had arrived on Linux, failing `test.yml`'s coverage job there (closes #334).

### `test.yml`'s aggregate runs `check_run_jobs.py`

The aggregate's step runs `check_run_jobs.py`, which reads the run's jobs
listing again up to a deadline while a row of `changes` or `coverage` is
unfinished (issue btclib-org/.github#1470).

### `mempool_ephemeral_dust`'s nonzero-dust body needs a newer btclib-node

The released build skips it too, like every other body of the file; only past
btclib-org/btclib-node#1332 does it pass, tested alone under
`-minrelaytxfee=0` (closes #316).

### `rpc_getchaintips` asks for `Capability.CHAIN_TIPS`

`Capability.CHAIN_TIPS` is new, declared by a btclib-node build serving it;
`wallet_signmessagewithaddress` asks for `SIGN_MESSAGE_WITH_PRIVKEY` too, and
`rpc_invalidateblock` reads a version off bitcoind alone (closes #339).

### The kept-connection test fails without `-rpcservertimeout=99000`

`test_a_kept_connection_outlives_the_default_server_timeout` checks that the
probe of a connection idle past bitcoind's default server timeout finds it
open, not only that the call after the idle is answered (closes #341).

### `feature_assumevalid` is ported

A node given `-assumevalid` at a buried spend's own hash skips checking it, one
given none catches it and marks the chain past it invalid.
`Capability.ASSUME_VALID` is new; btclib-node skips (issue #14).

### `BtclibNodeAdapter` declares `Capability.DISCONNECT` per build

An instance declares it where the build's own `rpc.callbacks.callbacks` names
`disconnectnode`, as btclib-node's `main` does and its release does not
(closes #297).

### `feature_rbf` is ported

A replacement pays for what it replaces or is refused, under either generation
of the rules. `Capability.INCREMENTAL_RELAY_FEE` is new; btclib-node skips
(issue #14).

### `NodeAdapter` waits for a node that exits on its own

`NodeAdapter.wait_until_stopped` returns the exit code and stderr of a node
that exits on its own; `p2p_headers_sync_with_minchainwork` waits with it for
a lagging clock's abort, on builds that carry it (closes #318).

### `p2p_permissions` is ported

`-whitelist` and `-whitebind` grant the permissions they name, read off
`getpeerinfo`, and a `forcerelay` peer has a mempool transaction relayed on.
`Capability.PEER_PERMISSIONS` is new; btclib-node skips (issue #14).

### `TF2.md`'s btclib-node `main` cells match a measured run

Rows a `main` now passes read `pass`, and the rows still failing cite an open
btclib-node issue; `feature_utxo_set_hash.py` cites a new one (closes #312).

### CLAUDE.md carries the shared primary-checkout section

The section is the organization's, word for word. Prose that named the
closed charter's steps as pending says what holds, and CLAUDE.md points
at the standard where it restated it (issue btclib-org/.github#1494).

### A test with a Core wait at or past the per-test bound has its own timeout

`pytest.mark.scaled_timeout(seconds)` gives a test a `timeout` scaled by
`--timeout-factor` like the ini one. Tests with a Core wait at or past the
bound use it, so a stalled wait fails with its own message (closes #309).

### `Dependency review` is a required check

- **`REPOSITORY.md` reads `lint / Dependency review` back with the other
  required checks** (issue btclib-org/.github#1465).

### The P2PK coin helpers live in one module

`tests/integration/p2pk_coins_test.py` holds what `p2p_orphan_handling`,
`p2p_opportunistic_1p1c` and `p2p_1p1c_network` kept as private copies
(issue #321).

### The test peers and the mock clock live in one module

`tests/integration/p2p_conns_test.py` holds the test peers, their
connection factories and the mock clock that p2p tests kept as
private copies (issue #321).

### The bitcoind adapter that drops its own `-bind` lives in one module

`tests/integration/bitcoind_adapters_test.py` holds `UnboundBitcoindAdapter`,
which `feature_port` and `p2p_getaddr_caching` kept as private copies
(closes #321).

### The leftover test peers are `Conn`s

p2p test helpers that repeated the receive and ping of `Conn`
(`tests/integration/p2p_conns_test.py`) now use it (closes #362).

### `[tool.uv] required-version` is `>=0.12.18`

- **`required-version` reads `>=0.12.18`, not `>=0.12.19`** (issue
  btclib-org/.github#1482): the Dependabot service refused `0.12.19` with
  `tool_version_not_supported`.

### The docs build survives an outage of `docs.python.org`

`docs/source/_inventories/python.inv` is Python 3.14's inventory, which
Sphinx reads only when `docs.python.org` fails to answer, so the `-n -W`
build no longer fails while that site is down (closes #365).

### `p2p_private_broadcast_retry_v1` ported, `-privatebroadcast` probed

The port runs a `Socks5Proxy` for `-proxy` and one for `-onion`. Where
`-help` lists no `-privatebroadcast`, `BitcoindAdapter` lacks
`PRIVATE_BROADCAST` and the private broadcast tests skip (issue #47).

### `rpc_net`'s single-node steps are ported

Core's `rpc_net` is checked for the steps that need one node, its dead
`-proxy` needing no mock; btclib-node skips (issue #47).

### `BitcoindAdapter` probes the cluster mempool

Where `-help-debug` lists no `-limitclustercount`, `BitcoindAdapter` lacks
the cluster capabilities and `mempool_cluster` skips; the mempool files
that branch on them assert the older limits there (issue #354).

### `feature_config_args`'s proxy steps are ported

Core's `feature_config_args` is checked for `-proxy` given no value, `-connect`
beside a proxy, and `-privatebroadcast` without a proxy; btclib-node skips. A
build's own wording of the `-proxyrandomize` warning is asserted (issue #47).

### A `Signed-off-by:` trailer on every commit of a pull request

- **`CONTRIBUTING.md`'s shared half asks every commit for the trailer, which
  `reusable-lint.yml`'s `Sign-off` job checks** (issue
  btclib-org/.github#1467).

### The script-verify-flag wording is read per build

`feature_nulldummy`, `_dersig`, `_cltv` and `_csv_activation` expect
`mandatory-script-verify-flag-failed` from a bitcoind before `v30.0`,
`block-script-verify-flag-failed` from it (bitcoin/bitcoin#33183, issue #354).

### `p2p_private_broadcast_cap` is ported

A build with no cap on the queue must take every submission.
`MiniWallet.new_utxos` hashes a transaction once, not once per output
(closes #47).

### The primary-checkout section uses one form for the checkout

- **The section writes the checkout as `"${checkout:?}"` throughout, says
  what `<scratchpad>` is and names the pull** (issue
  btclib-org/.github#1500).

### Two p2p tests assert a bitcoind's own behaviour before v31.0

`p2p_addr_selfannouncement` and `p2p_ibd_txrelay` assert a bitcoind's own
behaviour before `v31.0` (bitcoin/bitcoin#34146, bitcoin/bitcoin#34054),
and close the peer they open when they fail (issue #354).

### Tests read a bitcoind's own behaviour before the release that changed it

`feature_reindex_init`, `rpc_getdescriptorinfo`, `feature_reindex`,
`feature_presegwit_node_upgrade`, `p2p_i2p_sessions` and `rpc_uptime` expect an
older bitcoind's behaviour; the first skips before `v30.0` (issue #354).

### `p2p_leak_tx` and `p2p_tx_download` read `getpeerinfo`'s `inv` fields per build

`p2p_leak_tx`'s in-block check and `p2p_tx_download`'s inv-block check read
them only where the build's own `help getpeerinfo` names them
(bitcoin/bitcoin#33448, issue #354).

### `p2p_ibd_stalling` reads the stall log per-build

The log half of `p2p_ibd_stalling` does not expect `Stall started` absent from a
bitcoind before `v31.0`, which logs it again (bitcoin/bitcoin#32180, issue #354).

### `p2p_orphan_handling`'s maximal-package check restarts its node with a `-datacarriersize`

A bitcoind before `v30.0` refuses the `OP_RETURN` padding of the package's
last transaction by default (bitcoin/bitcoin#32406, issue #354).

### `feature_proxy` reads `-proxy`'s network suffix only where the build takes it

`BitcoindAdapter` declares `Capability.PROXY_PER_NETWORK` where `-help` shows
the suffix (bitcoin/bitcoin#32425). Without it the suffix test skips and the
malformed-`-proxy` test expects the build's port refusal (issue #354).

### Tests padding with `MiniWallet` start their node with a `-datacarriersize`

A bitcoind before `v30.0` refuses the `OP_RETURN` padding `target_vsize` adds.
These tests and `p2p_orphan_handling` start it with
`mini_wallet.PADDING_DATACARRIER_SIZE` (bitcoin/bitcoin#32406, issue #354).

### Two mempool tests assert the datacarrier policy per build

`mempool_datacarrier` and `mempool_accept` assert an older bitcoind's 83-byte,
one-output cap (bitcoin/bitcoin#32406, issue #354) and a `getmempoolinfo`
lacking `permitbaremultisig` and `maxdatacarriersize` (bitcoin/bitcoin#29954).

### `CONTRIBUTING.md` says the maintainer self-merges while the bot review is off

*The review* says no ack of record exists while `claude-review.yml` is off,
and that a local review of a named sha, by a reviewer other than the author,
stands in (issue btclib-org/.github#1527).

### `p2p_opportunistic_1p1c` asserts the 2p1c package and the orphanage per build

A bitcoind before `v30.0` refuses a child of two parents when one is in the
mempool (bitcoin/bitcoin#31385) and bounds the orphanage by count alone
(bitcoin/bitcoin#31829). Those checks assert what each build does (issue #354).

### `dependabot.yml` does not say that every workflow passes `--locked`

`.github/dependabot.yml` says the workflows install from `uv.lock` with
`--locked`, bar `node-integration.yml`, which installs btclib-node with `pip`
(issue btclib-org/.github#1538).

### `REPOSITORY.md` reads back the web sign-off setting

`REPOSITORY.md` reads `web_commit_signoff_required` back, the organization
setting section 11 of the standard states (issue btclib-org/.github#1540).

### The `Sign-off` check is required

`CONTRIBUTING.md`'s shared half says a pull request whose commits lack the
`Signed-off-by:` trailer cannot merge, and `REPOSITORY.md` lists
`lint / Sign-off` among the required checks (issue btclib-org/.github#1550).

### `CONTRIBUTING.md` says the ack of record is a bot's

*The review* says the maintainer lands their own pull requests through the
bypass, and that the `two_person_review` criterion is unmet because the ack
is a bot's (issue btclib-org/.github#452).

### `BtclibNodeAdapter` declares `Capability.V2TRANSPORT` for a build with `-v2transport`

A probe of `cli.build_config` declares it on btclib-node's `main` and not on
the PyPI release. The BIP324 test runs on `main` and checks that both ends
read one `session_id` (closes #392).

### `BtclibNodeAdapter` starts btclib-node with `-v1transport=1` if it can

A probe of `cli.build_config` decides, as for `-v2transport`. `Peer` speaks
v1 only, and btclib-node refuses v1 under `-v1transport=0`. A build without
the flag gets no `-v1transport` (closes #395).

### The maintainer's bypass is for emergencies

`CONTRIBUTING.md` and `REVIEWING.md` say every pull request, the maintainer's
included, lands with an approving review from somebody other than its author;
the bypass is for emergencies (issue btclib-org/.github#1362).

### Earlier entries on how a pull request lands

Entries above that have the maintainer landing without another person's approval
describe the rule before issue btclib-org/.github#1362 (issue
btclib-org/.github#1569).

### The forms set a type, and the history files lose merge=union

The issue forms set a type, not a kind label. `.gitattributes` goes, so a
rebase over a landing that wrote an entry stops on a conflict here
(issue btclib-org/.github#1584) (issue btclib-org/.github#1582).

### `--admin` waits for no required check

`CONTRIBUTING.md`'s emergency paragraph says `--admin` skips the
required checks too, and `REVIEWING.md`'s "hold the merge" excepts it
(issue btclib-org/.github#1597).

### Tests bound a wait by the timeout of what they test

`tests/peer_test.py` and `tests/socks5_test.py` bound a measured wait by
half the timeout the peer or proxy is given, not by seconds
(issue btclib-org/.github#1587).

### `check-changelog` refuses an entry added to an older release

- **A `###` heading under a release older than the newest, absent from
  the file at the merge base with `origin/main`, is refused**
  (issue btclib-org/.github#1614).

### A refused forwarding no longer depends on a free port

`test_what_forwarding_raises_is_queued_behind_the_request` has the
connection refused by a patched `socket.create_connection`, not by a freed
port another socket can take (closes #401).

### `BtclibNodeAdapter` declares `PERMIT_BARE_MULTISIG` per build

A probe of `cli.build_config` declares it on a build with `-permitbaremultisig`,
not `v2026.9.24`. The `mempool_accept`, `mempool_dust` and
`mempool_datacarrier` ports run past it; `TF2.md` records each (closes #411).

### `mempool_ephemeral_dust` and `p2p_1p1c_network` assert per-build policy

A bitcoind before `v31.0` refuses a package whose parent pays no fee
(bitcoin/bitcoin#33892) and checks a reorged child's dust spend
(bitcoin/bitcoin#33616); the bodies assert each (closes #354).

### `rpc_echo_payload` calls a libevent build over a connection per call

A bitcoind serving HTTP through libevent can leave a kept-alive connection
without a response, or part of one, until the call times out
(closes #415).

### `p2p_invalid_messages`' addrv2 checks are one body over both nodes

btclib-node answers a `ping` in the order received, so the `ping` proves the
`addrv2` before it was handled and no fixed wait precedes it (issue #125).

### The ported tests carry the cases Core added after their pins

The cases are those of bitcoin/bitcoin#34213, bitcoin/bitcoin#36426 and
bitcoin/bitcoin#36152, and the `TF2.md` rows move to the commits ported
(closes #407).
