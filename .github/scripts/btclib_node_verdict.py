# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

r"""Compare a btclib-node run of `tests/integration` with `TF2.md`'s column.

`node-integration.yml`'s `btclib-node` and `btclib-node-main` jobs run
every `*_btclib_node_test.py` module, and some of those tests fail by
design: a **fail** row of `TF2.md`'s per-test ledger names the issue it
is filed as (that ledger's own legend says which tracker), and a **not
ported** one is a stub ending in `pytest.fail`. So pytest's own exit
status says nothing about whether anything changed. This script says it
instead: each row of the ledger's `btclib-node` column against the
testcases of the JUnit report that row covers, printing every row whose
outcome moved.

**Which verdict a cell gives for which build.** A cell is either a
single verdict, true of every build, or two `; `-separated segments, in
the spelling the ledger's cells use:

- `<verdict> on the build` -- the PyPI release the `btclib-node` job
  installs (`release` below);
- `<verdict> on a build past [ISS ...](...)` -- a build carrying that
  issue's fix, which btclib-node's own `main` (the `btclib-node-main`
  job, `main` below) is read as being.

A cell keeps no verdict of an older build, and a segment bounded by a
second issue (`and before [ISS ...]`) is refused as any other shape the
ledger does not define.

A verdict is `pass`, `skip` with or without a parenthetical,
`fail ([ISS ...](...))`, `flaky ([ISS ...](...))` or `not ported`;
`bitcoind only` is a whole cell naming no btclib-node test at all. A
cell or a segment in any other shape raises `LedgerError` rather than
being read as some verdict: a cell this cannot read is a row nothing
compares. The parenthetical of a skip is the ledger's own shorthand
(`blk` for `Capability.BLK_FILES`), not a `Capability` value, so which
capability refused is not compared.

**What a row's outcome is.** A row's testcases are read together: the
row failed where any of them failed, skipped where none failed and any
skipped, and passed where every one passed. A **skip** row can hold a
passing test beside the skipping one -- `feature_blocksdir.py`'s own
refusal passes on btclib-node's `main` -- and a **fail** row needs one failing
test, not every one. A **fail** and a **not ported** row both expect
the row to fail. A **flaky** row expects it to pass or to fail, and
neither is a move; a skip there is one.

**Which testcase is which row's.** A JUnit testcase names a module and a
function, a ledger row a Core file and a qualifier, and nothing in either
names the other, so `_ROWS` states the pairing: a module stem, less its
`_btclib_node_test` suffix, for a module whose every test is one row's,
and `module::function` where one module's tests are several rows'. A
module with no ledger row at all is in `_NO_ROW`, and its testcases are
reported only where they fail. `btclib_node_verdict_test.py` holds the
table to the tree both ways: every test function of every
`*_btclib_node_test.py` module resolves, and every ledger row other than
**bitcoind only** is some test's.

**A teardown timeout is no verdict.** A node that ignores `terminate`
for the whole wait raises `TimeoutError` in the fixture's teardown
(btclib-org/btclib-node#1274), which pytest reports as an `<error>` of a
test whose call already ended. That error does not decide the row: the
call's own outcome does, and each such test is listed under a heading of
its own, which does not change the exit status.

**A flaky row that passed is listed apart.** It agrees with its cell, so
it is no move. `TF2.md`'s legend says when such a verdict moves to
**pass**, which turns on the issue the cell names, and this reads no
issue. Each flaky row this run passed is listed under a heading of its
own, which does not change the exit status.

It exits 1 wherever anything moved, a row that now passes included:
each move is a cell of `TF2.md` no longer describing that build, and a
green step then means the whole column does. This writes to stdout
alone, and the workflow step copies it into `$GITHUB_STEP_SUMMARY`:

    python .github/scripts/btclib_node_verdict.py \
        TF2.md integration.xml release
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

BUILDS = ("release", "main")

_SECTION = "## The per-test ledger"

_ISS = r"\[ISS [^\]]+\]\([^)\s]+\)"

# a verdict, and the outcome a row's testcases are expected to have
_VERDICTS = (
    (re.compile(r"pass"), "pass"),
    (re.compile(r"not ported"), "fail"),
    (re.compile(rf"fail \({_ISS}\)"), "fail"),
    (re.compile(rf"flaky \({_ISS}\)"), "flaky"),
    (re.compile(r"skip(?: \(\w+\))?"), "skip"),
)

_ON_THE_BUILD = re.compile(r"(?P<verdict>.+) on the build")
_ON_A_BUILD_PAST = re.compile(rf"(?P<verdict>.+) on a build past {_ISS}")

_BITCOIND_ONLY = "bitcoind only"

# the `<error>` pytest writes for the teardown timeout of
# btclib-org/btclib-node#1274
_TEARDOWN_TIMEOUT = re.compile(
    r'failed on teardown with "TimeoutError: node process ignored terminate for '
)
_TEARDOWN_ISSUE = "btclib-org/btclib-node#1274"

_SUFFIX = "_btclib_node_test"

# a module stem, or `stem::function`, against the ledger row its tests
# are -- the row's own first cell, verbatim. A function entry wins over
# its module's
_ROWS: dict[str, str] = {
    "feature_blocksdir": "`feature_blocksdir.py`",
    "feature_filelock": "`feature_filelock.py`",
    "rpc_whitelist": "`rpc_whitelist.py`",
    "rpc_whitelist::test_a_rejected_method_is_logged_as_named": (
        "`rpc_whitelist.py` (rejected method, log)"
    ),
    "rpc_users": "`rpc_users.py`",
    "rpc_users::test_norpcauth_disables_previous_rpcauth": (
        "`rpc_users.py` (`-norpcauth`)"
    ),
    "rpc_users::test_rpcuser_rpcpassword_authenticates_without_a_cookie": (
        "`rpc_users.py` (`-rpcuser`/`-rpcpassword`)"
    ),
    "rpc_users::test_norpccookiefile_writes_no_cookie_and_rpcauth_still_authenticates": (
        "`rpc_users.py` (`-norpccookiefile`)"
    ),
    "p2p_block_sync": "`p2p_block_sync.py`",
    "p2p_compactblocks_hb": "`p2p_compactblocks_hb.py`",
    "p2p_getdata": "`p2p_getdata.py`",
    "p2p_invalid_locator": "`p2p_invalid_locator.py`",
    "p2p_invalid_messages::test_wrong_magic_bytes_disconnects_the_peer": (
        "`p2p_invalid_messages.py` (wire)"
    ),
    "p2p_invalid_messages::test_wrong_magic_bytes_is_logged": (
        "`p2p_invalid_messages.py` (log)"
    ),
    "p2p_invalid_messages::test_oversized_message_disconnects_the_peer": (
        "`p2p_invalid_messages.py` (size, wire)"
    ),
    "p2p_invalid_messages::test_oversized_message_is_logged": (
        "`p2p_invalid_messages.py` (size, log)"
    ),
    "p2p_invalid_messages_misbehaving::test_oversized_inv_disconnects_the_peer": (
        "`p2p_invalid_messages.py` (inv, wire)"
    ),
    "p2p_invalid_messages_misbehaving::test_oversized_inv_is_logged": (
        "`p2p_invalid_messages.py` (inv, log)"
    ),
    "p2p_invalid_messages_misbehaving::test_oversized_getdata_disconnects_the_peer": (
        "`p2p_invalid_messages.py` (getdata, wire)"
    ),
    "p2p_invalid_messages_misbehaving::test_oversized_getdata_is_logged": (
        "`p2p_invalid_messages.py` (getdata, log)"
    ),
    "p2p_invalid_messages_misbehaving::test_oversized_headers_disconnects_the_peer": (
        "`p2p_invalid_messages.py` (headers, wire)"
    ),
    "p2p_invalid_messages_misbehaving::test_oversized_headers_is_logged": (
        "`p2p_invalid_messages.py` (headers, log)"
    ),
    "p2p_invalid_messages_misbehaving::test_invalid_pow_header_disconnects_the_peer": (
        "`p2p_invalid_messages.py` (invalid pow, wire)"
    ),
    "p2p_invalid_messages_misbehaving::test_invalid_pow_header_is_logged": (
        "`p2p_invalid_messages.py` (invalid pow, log)"
    ),
    "p2p_invalid_messages_misbehaving::test_noncontinuous_headers_disconnects_the_peer": (
        "`p2p_invalid_messages.py` (noncontinuous, wire)"
    ),
    "p2p_invalid_messages_misbehaving::test_noncontinuous_headers_is_logged": (
        "`p2p_invalid_messages.py` (noncontinuous, log)"
    ),
    "p2p_invalid_messages_dropped::test_duplicate_version_keeps_the_connection": (
        "`p2p_invalid_messages.py` (dup version, wire)"
    ),
    "p2p_invalid_messages_dropped::test_duplicate_version_is_logged": (
        "`p2p_invalid_messages.py` (dup version, log)"
    ),
    "p2p_invalid_messages_dropped::test_wrong_checksum_keeps_the_connection": (
        "`p2p_invalid_messages.py` (checksum, wire)"
    ),
    "p2p_invalid_messages_dropped::test_wrong_checksum_is_logged": (
        "`p2p_invalid_messages.py` (checksum, log)"
    ),
    "p2p_invalid_messages_dropped::test_invalid_msgtype_keeps_the_connection": (
        "`p2p_invalid_messages.py` (msgtype, wire)"
    ),
    "p2p_invalid_messages_dropped::test_invalid_msgtype_is_logged": (
        "`p2p_invalid_messages.py` (msgtype, log)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_empty_keeps_the_connection": (
        "`p2p_invalid_messages.py` (addrv2 empty, wire)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_empty_is_logged": (
        "`p2p_invalid_messages.py` (addrv2 empty, log)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_no_addresses_keeps_the_connection": (
        "`p2p_invalid_messages.py` (addrv2 no addr, wire)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_no_addresses_is_logged": (
        "`p2p_invalid_messages.py` (addrv2 no addr, log)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_too_long_address_keeps_the_connection": (
        "`p2p_invalid_messages.py` (addrv2 long, wire)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_too_long_address_is_logged": (
        "`p2p_invalid_messages.py` (addrv2 long, log)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_unrecognized_network_keeps_the_connection": (
        "`p2p_invalid_messages.py` (addrv2 net id, wire)"
    ),
    "p2p_invalid_messages_addrv2::test_addrv2_unrecognized_network_is_logged": (
        "`p2p_invalid_messages.py` (addrv2 net id, log)"
    ),
    "p2p_leak::test_obsolete_version_disconnects_the_peer": "`p2p_leak.py` (wire)",
    "p2p_leak::test_obsolete_version_is_logged": "`p2p_leak.py` (log)",
    "p2p_leak::test_feature_negotiation_starts_at_the_wtxid_version": (
        "`p2p_leak.py` (version boundary)"
    ),
    "p2p_handshake::test_redundant_verack_keeps_the_connection": (
        "`p2p_handshake.py` (wire)"
    ),
    "p2p_handshake::test_redundant_verack_is_logged": ("`p2p_handshake.py` (log)"),
    "p2p_handshake::test_outbound_services_decide_the_connection": (
        "`p2p_handshake.py` (services, wire)"
    ),
    "p2p_handshake::test_outbound_services_refusal_is_logged": (
        "`p2p_handshake.py` (services, log)"
    ),
    "p2p_handshake::test_limited_peer_is_kept_only_near_the_tip": (
        "`p2p_handshake.py` (limited, wire)"
    ),
    "p2p_handshake::test_limited_peer_refusal_is_logged": (
        "`p2p_handshake.py` (limited, log)"
    ),
    "p2p_handshake::test_feeler_is_dropped_after_its_version": (
        "`p2p_handshake.py` (feeler, wire)"
    ),
    "p2p_handshake::test_feeler_completion_is_logged": (
        "`p2p_handshake.py` (feeler, log)"
    ),
    "p2p_handshake::test_self_connection_is_dropped": (
        "`p2p_handshake.py` (self, wire)"
    ),
    "p2p_handshake::test_self_connection_is_logged": ("`p2p_handshake.py` (self, log)"),
    "p2p_addr_relay::test_oversized_addr_disconnects_the_peer": (
        "`p2p_addr_relay.py` (wire)"
    ),
    "p2p_addr_relay::test_oversized_addr_is_logged": ("`p2p_addr_relay.py` (log)"),
    "p2p_addrv2_relay::test_sendaddrv2_after_verack_disconnects_the_peer": (
        "`p2p_addrv2_relay.py` (wire)"
    ),
    "p2p_addrv2_relay::test_sendaddrv2_after_verack_is_logged": (
        "`p2p_addrv2_relay.py` (log)"
    ),
    "p2p_net_deadlock": "`p2p_net_deadlock.py`",
    "feature_uacomment": "`feature_uacomment.py`",
    "rpc_uptime": "`rpc_uptime.py`",
    "feature_framework_miniwallet": "`feature_framework_miniwallet.py`",
    "feature_framework_miniwallet::test_mini_wallet_confirmed_only_tells_mined_coins_from_mempool_ones": (
        "`feature_framework_miniwallet.py` (`confirmed_only`)"
    ),
    "feature_framework_miniwallet::test_mini_wallet_fee_rate_is_the_fee_the_node_reports": (
        "`feature_framework_miniwallet.py` (`fee_rate`)"
    ),
    "feature_framework_miniwallet::test_mini_wallet_version_3_is_held_to_truc_policy": (
        "`feature_framework_miniwallet.py` (TRUC)"
    ),
    "mempool_resurrect": "`mempool_resurrect.py`",
    "mempool_spend_coinbase": "`mempool_spend_coinbase.py`",
    "feature_dersig::test_dersig_activates_one_block_before_the_configured_height": (
        "`feature_dersig.py`"
    ),
    "feature_dersig::test_a_block_below_the_minimum_version_is_refused": (
        "`feature_dersig.py` (wire)"
    ),
    "feature_dersig::test_a_block_below_the_minimum_version_is_logged": (
        "`feature_dersig.py` (log)"
    ),
    "feature_dersig::test_a_non_der_signature_is_refused_once_active": (
        "`feature_dersig.py` (signature)"
    ),
    "feature_cltv::test_cltv_activates_one_block_before_the_configured_height": (
        "`feature_cltv.py`"
    ),
    "feature_cltv::test_a_version_3_block_is_refused_once_active": (
        "`feature_cltv.py` (wire)"
    ),
    "feature_cltv::test_a_version_3_block_is_logged_once_active": (
        "`feature_cltv.py` (log)"
    ),
    "feature_cltv::test_cltv_failures_are_mined_until_the_configured_height": (
        "`feature_cltv.py` (failures, activation)"
    ),
    "feature_cltv::test_cltv_failures_are_refused_by_the_mempool": (
        "`feature_cltv.py` (failures, mempool)"
    ),
    "feature_cltv::test_cltv_failures_are_refused_in_a_block": (
        "`feature_cltv.py` (failures, block)"
    ),
    "feature_csv_activation::test_csv_activates_one_block_before_the_configured_height": (
        "`feature_csv_activation.py`"
    ),
    "feature_csv_activation::test_csv_rules_are_enforced_from_the_configured_height": (
        "`feature_csv_activation.py` (lock times)"
    ),
    "feature_nulldummy": "`feature_nulldummy.py`",
    "feature_dirsymlinks": "`feature_dirsymlinks.py`",
    "feature_posix_fs_permissions": "`feature_posix_fs_permissions.py`",
    "rpc_createmultisig": "`rpc_createmultisig.py` (spend)",
    "rpc_setban::test_a_ban_drops_the_connection_it_matches": "`rpc_setban.py` (ban)",
    "rpc_setban::test_a_ban_survives_a_restart_until_it_is_removed": (
        "`rpc_setban.py` (restart)"
    ),
    "rpc_setban::test_a_noban_permission_reconnects_a_banned_peer": (
        "`rpc_setban.py` (noban)"
    ),
    "rpc_setban::test_a_non_ip_address_can_be_banned_and_unbanned": (
        "`rpc_setban.py` (non-IP)"
    ),
    "rpc_setban::test_bantime_given_at_a_restart_sets_a_new_ban_s_duration": (
        "`rpc_setban.py` (bantime)"
    ),
    "p2p_disconnect_ban": "`p2p_disconnect_ban.py` (disconnectnode)",
    "mempool_datacarrier": "`mempool_datacarrier.py`",
    "mempool_dust": "`mempool_dust.py`",
    "mempool_sigoplimit": "`mempool_sigoplimit.py`",
    "mempool_package_limits": "`mempool_package_limits.py`",
    "mempool_updatefromblock": "`mempool_updatefromblock.py`",
    "p2p_leak_tx::test_tx_in_block": "`p2p_leak_tx.py` (in block)",
    "p2p_leak_tx::test_notfound_on_replaced_tx": "`p2p_leak_tx.py` (replaced)",
    "p2p_leak_tx::test_notfound_on_unannounced_tx": "`p2p_leak_tx.py` (unannounced)",
    "feature_utxo_set_hash": "`feature_utxo_set_hash.py`",
    "rpc_getdescriptoractivity": "`rpc_getdescriptoractivity.py`",
    "rpc_getdescriptoractivity::test_activity_in_block": (
        "`rpc_getdescriptoractivity.py` (mempool)"
    ),
    "rpc_getdescriptoractivity::test_no_mempool_inclusion": (
        "`rpc_getdescriptoractivity.py` (mempool)"
    ),
    "rpc_getdescriptoractivity::test_multiple_addresses": (
        "`rpc_getdescriptoractivity.py` (mempool)"
    ),
    "rpc_getdescriptoractivity::test_confirmed_and_unconfirmed": (
        "`rpc_getdescriptoractivity.py` (mempool)"
    ),
    "rpc_getdescriptoractivity::test_receive_then_spend": (
        "`rpc_getdescriptoractivity.py` (mempool)"
    ),
    "rpc_getdescriptoractivity::test_no_address": (
        "`rpc_getdescriptoractivity.py` (mempool)"
    ),
    "rpc_getblockstats": "`rpc_getblockstats.py`",
    "feature_fastprune": "`feature_fastprune.py`",
    "rpc_scanblocks": "`rpc_scanblocks.py`",
    "rpc_scanblocks::test_scanblocks_refuses_without_the_index": (
        "`rpc_scanblocks.py` (no index)"
    ),
    "p2p_eviction": "`p2p_eviction.py`",
    "feature_presegwit_node_upgrade": "`feature_presegwit_node_upgrade.py`",
    "rpc_validateaddress": "`rpc_validateaddress.py`",
    "p2p_addrfetch": "`p2p_addrfetch.py`",
    "rpc_echo_payload": "`rpc_echo_payload.py`",
    "p2p_compactblocks_blocksonly": "`p2p_compactblocks_blocksonly.py`",
    "rpc_getblockfilter": "`rpc_getblockfilter.py`",
    "rpc_getblockfrompeer": "`rpc_getblockfrompeer.py`",
    "p2p_node_network_limited": "`p2p_node_network_limited.py`",
    "rpc_getdescriptorinfo": "`rpc_getdescriptorinfo.py`",
    "p2p_timeouts::test_peers_that_never_finish_the_handshake_are_dropped": (
        "`p2p_timeouts.py` (wire)"
    ),
    "p2p_timeouts::test_handshake_timeouts_are_logged": "`p2p_timeouts.py` (log)",
    "p2p_timeouts::test_a_non_positive_peertimeout_is_refused": (
        "`p2p_timeouts.py` (refusal)"
    ),
    "p2p_ping::test_ping_replies_are_reported_on_rpc": "`p2p_ping.py` (wire)",
    "p2p_ping::test_ping_replies_are_logged": "`p2p_ping.py` (log)",
    "mempool_expiry": "`mempool_expiry.py`",
    "p2p_add_connections": "`p2p_add_connections.py`",
    "p2p_add_connections::test_manual_connection_past_the_outbound_capacity": (
        "`p2p_add_connections.py` (`manual`)"
    ),
    "p2p_v2_encrypted::test_inbound_connections_and_blocks": (
        "`p2p_v2_encrypted.py` (inbound)"
    ),
    "p2p_v2_encrypted::test_outbound_connections": "`p2p_v2_encrypted.py` (outbound)",
    "feature_includeconf::test_includeconf_files_are_read_in_order": (
        "`feature_includeconf.py` (order)"
    ),
    "feature_includeconf::test_noincludeconf_0_on_the_command_line_is_refused": (
        "`feature_includeconf.py` (double negative)"
    ),
    "feature_includeconf::test_includeconf_on_the_command_line_is_refused": (
        "`feature_includeconf.py` (`-includeconf`)"
    ),
    "feature_includeconf::test_a_nested_includeconf_is_ignored_with_a_warning": (
        "`feature_includeconf.py` (nested)"
    ),
    "feature_includeconf::test_a_missing_included_file_is_refused": (
        "`feature_includeconf.py` (missing)"
    ),
    "feature_reindex_init": "`feature_reindex_init.py`",
    "rpc_generate": "`rpc_generate.py`",
    "rpc_signrawtransactionwithkey": "`rpc_signrawtransactionwithkey.py`",
    "rpc_scantxoutset": "`rpc_scantxoutset.py`",
    "feature_proxy": "`feature_proxy.py`",
    "feature_proxy::test_cjdnsreachable_reaches_cjdns_through_the_proxy": (
        "`feature_proxy.py` (`-cjdnsreachable`)"
    ),
    "feature_proxy::test_i2psam_is_the_proxy_of_i2p_alone": (
        "`feature_proxy.py` (`-i2psam`)"
    ),
    "feature_proxy::test_malformed_i2psam_is_refused": "`feature_proxy.py` (`-i2psam`)",
    "feature_proxy::test_onlynet_refuses_a_network_it_cannot_reach": (
        "`feature_proxy.py` (`-onlynet`)"
    ),
    "wallet_signmessagewithaddress": "`wallet_signmessagewithaddress.py`",
    "wallet_blank": "`wallet_blank.py`",
    "wallet_coinbase_category": "`wallet_coinbase_category.py`",
    "p2p_initial_headers_sync": "`p2p_initial_headers_sync.py`",
    "p2p_initial_headers_sync::test_headers_timeout_drops_the_peer": (
        "`p2p_initial_headers_sync.py` (stall, wire)"
    ),
    "p2p_initial_headers_sync::test_headers_timeout_is_logged": (
        "`p2p_initial_headers_sync.py` (stall, log)"
    ),
    "p2p_initial_headers_sync::test_headers_timeout_keeps_a_noban_peer": (
        "`p2p_initial_headers_sync.py` (noban, wire)"
    ),
    "p2p_initial_headers_sync::test_headers_timeout_of_a_noban_peer_is_logged": (
        "`p2p_initial_headers_sync.py` (noban, log)"
    ),
    "p2p_sendtxrcncl": "`p2p_sendtxrcncl.py`",
    "p2p_sendtxrcncl::test_sendtxrcncl_is_not_sent_with_bloom_filters_and_no_relay": (
        "`p2p_sendtxrcncl.py` (bloom)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_is_sent_to_full_relay_outbound_peers": (
        "`p2p_sendtxrcncl.py` (outbound)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_on_block_relay_only_is_logged": (
        "`p2p_sendtxrcncl.py` (outbound, log)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_is_not_sent_in_blocks_only_mode": (
        "`p2p_sendtxrcncl.py` (blocksonly)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_is_ignored_without_the_option": (
        "`p2p_sendtxrcncl.py` (off)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_is_ignored_without_the_option_in_the_log": (
        "`p2p_sendtxrcncl.py` (off, log)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_violations_drop_the_peer": (
        "`p2p_sendtxrcncl.py` (violations, wire)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_violations_are_logged": (
        "`p2p_sendtxrcncl.py` (violations, log)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_kept_peers_stay_connected": (
        "`p2p_sendtxrcncl.py` (kept, wire)"
    ),
    "p2p_sendtxrcncl::test_sendtxrcncl_kept_peers_are_logged": (
        "`p2p_sendtxrcncl.py` (kept, log)"
    ),
    "feature_reindex::test_a_reindex_restores_the_height": (
        "`feature_reindex.py` (reindex)"
    ),
    "feature_reindex::test_blocks_out_of_order_are_reindexed": (
        "`feature_reindex.py` (out of order)"
    ),
    "feature_reindex::test_an_interrupted_reindex_keeps_its_index": (
        "`feature_reindex.py` (interrupted)"
    ),
    "feature_reindex_readonly": "`feature_reindex_readonly.py`",
    "p2p_feefilter": "`p2p_feefilter.py`",
    "p2p_feefilter::test_feefilter_is_not_sent_to_a_forcerelay_peer": (
        "`p2p_feefilter.py` (forcerelay)"
    ),
    "p2p_feefilter::test_feefilter_filters_announcements": (
        "`p2p_feefilter.py` (filter)"
    ),
    "p2p_feefilter::test_feefilter_is_not_sent_to_a_block_relay_only_peer": (
        "`p2p_feefilter.py` (block-relay-only)"
    ),
    "p2p_feefilter::test_feefilter_is_not_sent_in_blocks_only_mode": (
        "`p2p_feefilter.py` (blocksonly)"
    ),
    "p2p_mutated_blocks::test_mutated_block_keeps_the_honest_request": (
        "`p2p_mutated_blocks.py` (wire)"
    ),
    "p2p_mutated_blocks::test_mutated_block_is_logged": (
        "`p2p_mutated_blocks.py` (log)"
    ),
    "p2p_mutated_blocks::test_block_missing_its_parent_drops_the_peer": (
        "`p2p_mutated_blocks.py` (missing parent, wire)"
    ),
    "p2p_mutated_blocks::test_block_missing_its_parent_is_logged": (
        "`p2p_mutated_blocks.py` (missing parent, log)"
    ),
    "feature_anchors::test_block_relay_only_peers_are_the_anchors": (
        "`feature_anchors.py`"
    ),
    "feature_anchors::test_onion_anchor_is_dumped_and_dialled": (
        "`feature_anchors.py` (onion)"
    ),
    "p2p_addr_selfannouncement::test_self_announcement_to_inbound_peers": (
        "`p2p_addr_selfannouncement.py` (inbound, wire)"
    ),
    "p2p_addr_selfannouncement::test_self_announcement_to_inbound_peers_is_logged": (
        "`p2p_addr_selfannouncement.py` (inbound, log)"
    ),
    "p2p_addr_selfannouncement::test_self_announcement_to_outbound_peers": (
        "`p2p_addr_selfannouncement.py` (outbound, wire)"
    ),
    "p2p_addr_selfannouncement::test_self_announcement_to_outbound_peers_is_logged": (
        "`p2p_addr_selfannouncement.py` (outbound, log)"
    ),
    "p2p_addr_selfannouncement::test_externalip_bypasses_onlynet": (
        "`p2p_addr_selfannouncement.py` (`-onlynet`)"
    ),
    "p2p_message_capture": "`p2p_message_capture.py`",
    "feature_blocksxor": "`feature_blocksxor.py`",
    "wallet_createwalletdescriptor": "`wallet_createwalletdescriptor.py`",
    "wallet_sendmany": "`wallet_sendmany.py`",
    "wallet_timelock": "`wallet_timelock.py`",
    "mempool_accept_wtxid": "`mempool_accept_wtxid.py`",
    "rpc_orphans": "`rpc_orphans.py`",
    "wallet_simulaterawtx": "`wallet_simulaterawtx.py`",
    "wallet_rescan_unconfirmed": "`wallet_rescan_unconfirmed.py`",
    "mining_template_verification": "`mining_template_verification.py`",
    "feature_remove_pruned_files_on_startup": (
        "`feature_remove_pruned_files_on_startup.py`"
    ),
    "feature_prune_stale_fork": "`feature_prune_stale_fork.py`",
    "p2p_i2p_ports": "`p2p_i2p_ports.py`",
    "p2p_i2p_sessions": "`p2p_i2p_sessions.py`",
    "p2p_dns_seeds": "`p2p_dns_seeds.py`",
    "p2p_seednode": "`p2p_seednode.py`",
    "p2p_ibd_stalling::test_stalling_drops_the_staller": (
        "`p2p_ibd_stalling.py` (wire)"
    ),
    "p2p_ibd_stalling::test_stalling_is_logged": "`p2p_ibd_stalling.py` (log)",
    "p2p_ibd_stalling::test_manual_peer_stalling_pauses_the_peer": (
        "`p2p_ibd_stalling.py` (`manual`, wire)"
    ),
    "p2p_ibd_stalling::test_manual_peer_stalling_is_logged": (
        "`p2p_ibd_stalling.py` (`manual`, log)"
    ),
    "p2p_private_broadcast": "`p2p_private_broadcast.py`",
    "p2p_tx_download::test_an_expired_request_falls_back_to_another_peer": (
        "`p2p_tx_download.py` (expiry)"
    ),
    "p2p_tx_download::test_a_disconnect_falls_back_to_another_peer": (
        "`p2p_tx_download.py` (disconnect)"
    ),
    "p2p_tx_download::test_a_notfound_falls_back_to_another_peer": (
        "`p2p_tx_download.py` (notfound)"
    ),
    "p2p_tx_download::test_a_ready_preferred_peer_is_asked_first": (
        "`p2p_tx_download.py` (tiebreak)"
    ),
    "p2p_tx_download::test_an_inbound_peer_is_asked_after_the_delay": (
        "`p2p_tx_download.py` (inbound)"
    ),
    "p2p_tx_download::test_an_outbound_peer_is_asked_at_once": (
        "`p2p_tx_download.py` (outbound)"
    ),
    "p2p_tx_download::test_a_noban_peer_is_asked_at_once": (
        "`p2p_tx_download.py` (noban)"
    ),
    "p2p_tx_download::test_a_txid_peer_is_asked_at_once_without_a_wtxid_peer": (
        "`p2p_tx_download.py` (txid)"
    ),
    "p2p_tx_download::test_a_txid_peer_waits_beside_a_wtxid_peer": (
        "`p2p_tx_download.py` (txid beside wtxid)"
    ),
    "p2p_tx_download::test_a_large_inv_is_capped_without_the_relay_permission": (
        "`p2p_tx_download.py` (large inv)"
    ),
    "p2p_tx_download::test_duplicate_inv_entries_are_processed_once": (
        "`p2p_tx_download.py` (duplicate inv)"
    ),
    "p2p_tx_download::test_a_spurious_notfound_is_ignored": (
        "`p2p_tx_download.py` (spurious notfound)"
    ),
    "p2p_tx_download::test_requests_in_flight_are_capped": (
        "`p2p_tx_download.py` (in flight)"
    ),
    "p2p_tx_download::test_a_tx_reaches_a_node_past_unresponsive_peers": (
        "`p2p_tx_download.py` (inv block)"
    ),
    "p2p_tx_download::test_every_announcing_peer_is_asked_in_turn": (
        "`p2p_tx_download.py` (tx requests)"
    ),
    "p2p_tx_download::test_a_rejected_tx_is_asked_for_again_after_a_block": (
        "`p2p_tx_download.py` (rejects)"
    ),
    "p2p_tx_download::test_an_inv_of_the_wrong_kind_is_ignored": (
        "`p2p_tx_download.py` (mismatch)"
    ),
    "p2p_blocksonly::test_a_blocksonly_node_refuses_transactions": (
        "`p2p_blocksonly.py` (`-blocksonly`, wire)"
    ),
    "p2p_blocksonly::test_a_blocksonly_node_refusal_is_logged": (
        "`p2p_blocksonly.py` (`-blocksonly`, log)"
    ),
    "p2p_blocksonly::test_a_block_relay_only_peer_is_refused_transactions": (
        "`p2p_blocksonly.py` (block-relay-only, wire)"
    ),
    "p2p_blocksonly::test_a_block_relay_only_peer_refusal_is_logged": (
        "`p2p_blocksonly.py` (block-relay-only, log)"
    ),
    "p2p_orphan_handling::test_parents_arriving_during_the_delay_are_not_requested": (
        "`p2p_orphan_handling.py` (arrival timing)"
    ),
    "p2p_orphan_handling::test_a_rejected_parent_is_requested_only_under_another_witness": (
        "`p2p_orphan_handling.py` (rejected parents)"
    ),
    "p2p_orphan_handling::test_parents_not_recently_confirmed_are_requested": (
        "`p2p_orphan_handling.py` (multiple parents)"
    ),
    "p2p_orphan_handling::test_parents_already_requested_are_not_requested_again": (
        "`p2p_orphan_handling.py` (overlapping parents)"
    ),
    "p2p_orphan_handling::test_a_parent_kept_as_an_orphan_is_not_requested": (
        "`p2p_orphan_handling.py` (orphan of orphan)"
    ),
    "p2p_orphan_handling::test_an_orphan_is_reconsidered_once_its_parent_is_mined": (
        "`p2p_orphan_handling.py` (parent confirmed)"
    ),
    "p2p_orphan_handling::test_descendants_of_a_rejected_parent_are_rejected_too": (
        "`p2p_orphan_handling.py` (inherit rejection)"
    ),
    "p2p_orphan_handling::test_an_orphan_of_the_same_txid_is_kept_too": (
        "`p2p_orphan_handling.py` (same txid)"
    ),
    "p2p_orphan_handling::test_a_parent_of_the_same_txid_is_requested_again": (
        "`p2p_orphan_handling.py` (same txid parent)"
    ),
    "p2p_orphan_handling::test_an_inv_by_an_orphan_txid_is_requested": (
        "`p2p_orphan_handling.py` (txid inv)"
    ),
    "p2p_orphan_handling::test_an_outbound_announcer_is_asked_for_parents_first": (
        "`p2p_orphan_handling.py` (prefer outbound)"
    ),
    "p2p_orphan_handling::test_every_announcer_is_asked_for_parents": (
        "`p2p_orphan_handling.py` (announcers)"
    ),
    "p2p_orphan_handling::test_a_parent_gone_missing_is_requested": (
        "`p2p_orphan_handling.py` (parents change)"
    ),
    "p2p_orphan_handling::test_a_maximal_ancestor_package_is_protected_in_the_orphanage": (
        "`p2p_orphan_handling.py` (maximal package)"
    ),
    "p2p_compactblocks::test_sendcmpct_negotiates_compact_announcements": (
        "`p2p_compactblocks.py` (sendcmpct)"
    ),
    "p2p_compactblocks::test_a_compact_block_is_built_as_bip152_says": (
        "`p2p_compactblocks.py` (construction)"
    ),
    "p2p_compactblocks::test_an_announced_block_is_asked_for_compact": (
        "`p2p_compactblocks.py` (requests)"
    ),
    "p2p_compactblocks::test_only_missing_transactions_are_asked_for": (
        "`p2p_compactblocks.py` (getblocktxn requests)"
    ),
    "p2p_compactblocks::test_getblocktxn_is_answered_near_the_tip": (
        "`p2p_compactblocks.py` (getblocktxn handler)"
    ),
    "p2p_compactblocks::test_a_block_off_the_tip_is_not_sent_compact": (
        "`p2p_compactblocks.py` (not at tip)"
    ),
    "p2p_compactblocks::test_a_wrong_blocktxn_falls_back_to_the_block": (
        "`p2p_compactblocks.py` (incorrect blocktxn)"
    ),
    "p2p_compactblocks::test_a_submitted_block_is_announced_compact": (
        "`p2p_compactblocks.py` (end to end)"
    ),
    "p2p_compactblocks::test_a_low_work_cmpctblock_is_ignored": (
        "`p2p_compactblocks.py` (low work, wire)"
    ),
    "p2p_compactblocks::test_a_low_work_cmpctblock_is_logged": (
        "`p2p_compactblocks.py` (low work, log)"
    ),
    "p2p_compactblocks::test_invalid_transactions_in_a_cmpctblock_keep_the_peer": (
        "`p2p_compactblocks.py` (invalid tx)"
    ),
    "p2p_compactblocks::test_an_empty_getblocktxn_drops_the_peer": (
        "`p2p_compactblocks.py` (empty getblocktxn, wire)"
    ),
    "p2p_compactblocks::test_an_empty_getblocktxn_is_logged": (
        "`p2p_compactblocks.py` (empty getblocktxn, log)"
    ),
    "p2p_compactblocks::test_a_second_blocktxn_drops_the_peer": (
        "`p2p_compactblocks.py` (multiple blocktxn, wire)"
    ),
    "p2p_compactblocks::test_a_second_blocktxn_is_logged": (
        "`p2p_compactblocks.py` (multiple blocktxn, log)"
    ),
    "p2p_compactblocks::test_an_invalid_sendcmpct_announce_drops_the_peer": (
        "`p2p_compactblocks.py` (invalid sendcmpct, wire)"
    ),
    "p2p_compactblocks::test_an_invalid_sendcmpct_announce_is_logged": (
        "`p2p_compactblocks.py` (invalid sendcmpct, log)"
    ),
    "p2p_compactblocks::test_an_invalid_cmpctblock_drops_the_peer": (
        "`p2p_compactblocks.py` (invalid cmpctblock)"
    ),
    "p2p_compactblocks::test_an_invalid_optimistic_cmpctblock_drops_the_peer": (
        "`p2p_compactblocks.py` (invalid cmpctblock, optimistic)"
    ),
    "p2p_compactblocks::test_sendcmpct_negotiates_over_an_outbound_peer": (
        "`p2p_compactblocks.py` (sendcmpct, outbound)"
    ),
    "p2p_compactblocks::test_a_stalling_peer_leaves_the_block_to_another": (
        "`p2p_compactblocks.py` (stalling peer)"
    ),
    "p2p_compactblocks::test_the_last_reconstruction_is_kept_for_an_outbound_peer": (
        "`p2p_compactblocks.py` (parallel reconstruction)"
    ),
    "p2p_compactblocks::test_getpeerinfo_reports_high_bandwidth_states": (
        "`p2p_compactblocks.py` (high-bandwidth states)"
    ),
    "p2p_compactblocks::test_unsolicited_cmpctblocks_are_ignored": (
        "`p2p_compactblocks.py` (ignored)"
    ),
    "wallet_miniscript_decaying_multisig_descriptor_psbt": (
        "`wallet_miniscript_decaying_multisig_descriptor_psbt.py`"
    ),
    "wallet_anchor": "`wallet_anchor.py`",
    "feature_startupnotify": "`feature_startupnotify.py`",
    "rpc_dumptxoutset": "`rpc_dumptxoutset.py`",
    "feature_loadblock": "`feature_loadblock.py`",
    "feature_port": "`feature_port.py`",
    "p2p_opportunistic_1p1c::test_a_rejected_parent_is_taken_in_with_its_child": (
        "`p2p_opportunistic_1p1c.py` (parent first)"
    ),
    "p2p_opportunistic_1p1c::test_a_rejected_parent_with_no_witness_is_taken_in_with_its_child": (
        "`p2p_opportunistic_1p1c.py` (parent first, P2PK)"
    ),
    "p2p_opportunistic_1p1c::test_an_orphan_is_taken_in_with_its_low_fee_parent": (
        "`p2p_opportunistic_1p1c.py` (child first)"
    ),
    "p2p_opportunistic_1p1c::test_a_rejected_parent_is_taken_in_only_with_a_child_paying_enough": (
        "`p2p_opportunistic_1p1c.py` (low, high child)"
    ),
    "p2p_opportunistic_1p1c::test_a_rejected_parent_with_no_witness_is_taken_in_only_with_a_child_paying_enough": (
        "`p2p_opportunistic_1p1c.py` (low, high, P2PK)"
    ),
    "p2p_opportunistic_1p1c::test_parent_and_child_are_evaluated_together_only_from_one_peer": (
        "`p2p_opportunistic_1p1c.py` (orphan invalid)"
    ),
    "p2p_opportunistic_1p1c::test_an_invalid_parent_from_another_peer_leaves_the_orphan": (
        "`p2p_opportunistic_1p1c.py` (parent invalid)"
    ),
    "p2p_opportunistic_1p1c::test_no_rejected_parent_of_a_two_parent_orphan_is_requested": (
        "`p2p_opportunistic_1p1c.py` (multiple parents)"
    ),
    "p2p_opportunistic_1p1c::test_an_orphan_is_taken_in_with_one_parent_beside_another_in_the_mempool": (
        "`p2p_opportunistic_1p1c.py` (parent in mempool)"
    ),
    "p2p_opportunistic_1p1c::test_a_package_is_taken_in_on_top_of_another": (
        "`p2p_opportunistic_1p1c.py` (1p1c on 1p1c)"
    ),
    "p2p_opportunistic_1p1c::test_an_orphan_outlives_large_orphans_from_other_peers": (
        "`p2p_opportunistic_1p1c.py` (DoS, large orphans)"
    ),
    "p2p_opportunistic_1p1c::test_an_orphan_outlives_many_orphans_from_other_peers": (
        "`p2p_opportunistic_1p1c.py` (DoS, many orphans)"
    ),
    "p2p_block_times": "`p2p_block_times.py`",
    "p2p_outbound_eviction::test_lagging_unprotected_peers_are_evicted": (
        "`p2p_outbound_eviction.py` (unprotected)"
    ),
    "p2p_outbound_eviction::test_protected_peer_is_not_evicted": (
        "`p2p_outbound_eviction.py` (protected)"
    ),
    "p2p_outbound_eviction::test_only_misbehaving_unprotected_peers_are_evicted": (
        "`p2p_outbound_eviction.py` (mixed)"
    ),
    "p2p_outbound_eviction::test_block_relay_only_peer_is_not_protected": (
        "`p2p_outbound_eviction.py` (block-relay-only)"
    ),
    "p2p_tx_privacy": "`p2p_tx_privacy.py`",
    "p2p_invalid_block::test_invalid_blocks_are_refused": (
        "`p2p_invalid_block.py` (wire)"
    ),
    "p2p_invalid_block::test_invalid_blocks_are_logged": (
        "`p2p_invalid_block.py` (log)"
    ),
    "feature_maxtipage": "`feature_maxtipage.py`",
    "p2p_blockfilters": "`p2p_blockfilters.py`",
    "p2p_getaddr_caching": "`p2p_getaddr_caching.py`",
    "mempool_reorg::test_reorgs_evict_immature_and_non_final_spends": (
        "`mempool_reorg.py` (coinbase)"
    ),
    "mempool_reorg::test_disconnected_transactions_are_available_for_relay": (
        "`mempool_reorg.py` (relay)"
    ),
    "interface_rpc::test_getrpcinfo_names_the_call_running_and_the_log": (
        "`interface_rpc.py` (getrpcinfo)"
    ),
    "interface_rpc::test_a_batch_is_answered_member_by_member": (
        "`interface_rpc.py` (batch)"
    ),
    "interface_rpc::test_each_version_is_answered_with_its_own_status": (
        "`interface_rpc.py` (status codes)"
    ),
    "interface_rpc::test_a_notification_runs_and_is_answered_with_no_content": (
        "`interface_rpc.py` (notifications)"
    ),
    "interface_rpc::test_a_full_work_queue_refuses_the_request_beyond_it": (
        "`interface_rpc.py` (work queue)"
    ),
    "p2p_ibd_txrelay::test_ibd_tx_relay_is_withheld": "`p2p_ibd_txrelay.py` (wire)",
    "p2p_ibd_txrelay::test_ibd_tx_relay_is_logged": "`p2p_ibd_txrelay.py` (log)",
    "feature_bip68_sequence": "`feature_bip68_sequence.py`",
    "mempool_accept": "`mempool_accept.py`",
    "mempool_cluster": "`mempool_cluster.py`",
    "feature_minchainwork": "`feature_minchainwork.py`",
    "feature_minchainwork::test_outbound_peers_with_too_little_work_are_dropped_in_ibd": (
        "`feature_minchainwork.py` (outbound)"
    ),
    "mempool_packages": "`mempool_packages.py`",
    "feature_versionbits_warning": "`feature_versionbits_warning.py`",
    "mempool_package_rbf": "`mempool_package_rbf.py`",
    "p2p_headers_sync_with_minchainwork": "`p2p_headers_sync_with_minchainwork.py`",
    "p2p_unrequested_blocks": "`p2p_unrequested_blocks.py`",
    "p2p_1p1c_network": "`p2p_1p1c_network.py`",
    "mempool_ephemeral_dust": "`mempool_ephemeral_dust.py`",
    "mempool_ephemeral_dust::test_any_single_dust_output_is_allowed_alone": (
        "`mempool_ephemeral_dust.py` (nonzero dust)"
    ),
    "mempool_ephemeral_dust::test_a_reorg_returns_dust_to_the_mempool_unchecked": (
        "`mempool_ephemeral_dust.py` (reorg)"
    ),
    "feature_notifications::test_every_new_tip_is_notified": (
        "`feature_notifications.py` (`-blocknotify`)"
    ),
    "feature_notifications::test_a_large_work_invalid_chain_is_alerted": (
        "`feature_notifications.py` (`-alertnotify`)"
    ),
    "feature_notifications::test_the_shutdown_is_notified": (
        "`feature_notifications.py` (`-shutdownnotify`)"
    ),
    "feature_settings": "`feature_settings.py`",
    "rpc_getchaintips": "`rpc_getchaintips.py`",
    "rpc_preciousblock": "`rpc_preciousblock.py`",
    "rpc_invalidateblock": "`rpc_invalidateblock.py`",
    "rpc_signmessagewithprivkey": "`rpc_signmessagewithprivkey.py`",
    "feature_chain_tiebreaks": "`feature_chain_tiebreaks.py`",
    "p2p_sendheaders": "`p2p_sendheaders.py`",
    "p2p_fingerprint": "`p2p_fingerprint.py`",
    "rpc_estimatefee": "`rpc_estimatefee.py`",
    "feature_assumevalid": "`feature_assumevalid.py`",
    "feature_rbf": "`feature_rbf.py`",
    "p2p_permissions": "`p2p_permissions.py`",
    "p2p_private_broadcast_cap": "`p2p_private_broadcast_cap.py`",
    "p2p_private_broadcast_retry_v1": "`p2p_private_broadcast_retry_v1.py`",
    "rpc_net::test_a_node_is_added_listed_and_removed": ("`rpc_net.py` (`addnode`)"),
    "rpc_net::test_a_peer_s_service_flags_are_named": ("`rpc_net.py` (service flags)"),
    "rpc_net::test_getnodeaddresses_answers_from_the_address_table": (
        "`rpc_net.py` (`getnodeaddresses`)"
    ),
    "rpc_net::test_addpeeraddress_fills_the_address_tables": (
        "`rpc_net.py` (`addpeeraddress`)"
    ),
    "rpc_net::test_getaddrmaninfo_counts_the_addresses_of_each_network": (
        "`rpc_net.py` (`getaddrmaninfo`)"
    ),
    "rpc_net::test_getrawaddrman_lists_the_address_tables": (
        "`rpc_net.py` (`getrawaddrman`)"
    ),
    "feature_config_args::test_a_proxy_without_a_value_is_refused": (
        "`feature_config_args.py` (`-proxy`)"
    ),
    "feature_config_args::test_a_connect_node_ignores_the_seednode_and_the_dnsseed_beside_a_proxy": (
        "`feature_config_args.py` (`-connect`)"
    ),
    "feature_config_args::test_private_broadcast_is_refused_without_a_proxy_and_warns_without_randomizing": (
        "`feature_config_args.py` (`-privatebroadcast`)"
    ),
    "p2p_nobloomfilter_messages::test_filtered_block_request_disconnects_the_peer": (
        "`p2p_nobloomfilter_messages.py` (filtered block, wire)"
    ),
    "p2p_nobloomfilter_messages::test_filtered_block_request_is_logged": (
        "`p2p_nobloomfilter_messages.py` (filtered block, log)"
    ),
    "interface_http::test_excessive_request_size_is_refused": (
        "`interface_http.py` (excessive request size, wire)"
    ),
    "interface_http::test_excessive_request_size_is_logged": (
        "`interface_http.py` (excessive request size, log)"
    ),
    "feature_block::test_blocks_are_judged": "`feature_block.py` (wire)",
    "feature_block::test_blocks_are_judged_and_logged": "`feature_block.py` (log)",
}

# the modules whose tests are this repository's own harness rather than
# a port of a Core test, so no ledger row covers them
_NO_ROW = frozenset(
    {
        "adapter_lifecycle",
        "chain_selection",
        "mempool_fill",
        "mixed_cluster_block_sync",
        "v2transport_option",
    }
)


class LedgerError(ValueError):
    """A per-test ledger row, or a `btclib-node` cell, this cannot read."""


@dataclass(frozen=True)
class Expected:
    """What a ledger cell expects of a row's testcases on one build.

    :param text: the cell's own verdict for that build, as the ledger
        spells it.
    :param outcome: `"pass"`, `"fail"`, `"skip"`, or `"flaky"`, which
        a row's pass and its fail both agree with.
    """

    text: str
    outcome: str


def _verdict(row: str, text: str) -> Expected:
    """Read one verdict, less any build qualifier.

    :param row: the row's own first cell, named in a refusal.
    :param text: the verdict.
    :returns: the verdict and the outcome it expects.
    :raises LedgerError: where the text is no verdict the ledger defines.
    """
    for pattern, outcome in _VERDICTS:
        if pattern.fullmatch(text):
            return Expected(text, outcome)
    msg = f"{row}: no verdict reads {text!r}"
    raise LedgerError(msg)


def expected(row: str, cell: str, build: str) -> Expected | None:
    """Return what a `btclib-node` cell expects on one build.

    :param row: the row's own first cell, named in a refusal.
    :param cell: the row's `btclib-node` cell.
    :param build: `"release"` or `"main"`.
    :returns: the verdict for that build, or None for **bitcoind only**.
    :raises LedgerError: where a segment is in no shape the ledger
        defines, or where the cell does not give that build exactly one
        verdict.
    """
    if cell == _BITCOIND_ONLY:
        return None
    segments = cell.split("; ")
    if len(segments) == 1 and " on " not in cell:
        return _verdict(row, cell)
    found: list[str] = []
    for segment in segments:
        if match := _ON_THE_BUILD.fullmatch(segment):
            if build == "release":
                found.append(match["verdict"])
        elif match := _ON_A_BUILD_PAST.fullmatch(segment):
            if build == "main":
                found.append(match["verdict"])
        else:
            msg = f"{row}: no build qualifier reads {segment!r}"
            raise LedgerError(msg)
    if len(found) != 1:
        msg = f"{row}: {cell!r} gives the {build} build {len(found)} verdicts"
        raise LedgerError(msg)
    return _verdict(row, found[0])


def ledger_cells(ledger_text: str) -> dict[str, str]:
    """Return each per-test ledger row's first cell, with its btclib-node cell.

    :param ledger_text: `TF2.md`'s own text.
    :returns: each row's first cell against its `btclib-node` cell.
    :raises LedgerError: where the section is missing, where a row does
        not have the table's own width, or where two rows share a first
        cell.
    """
    if _SECTION not in ledger_text:
        msg = f"no {_SECTION!r} section"
        raise LedgerError(msg)
    section = ledger_text.split(_SECTION, 1)[1].split("\n## ", 1)[0]
    cells: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        row = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(row) != 5:
            msg = f"a row of {len(row)} cells, not five: {line!r}"
            raise LedgerError(msg)
        if row[0] in cells:
            msg = f"two rows read {row[0]}"
            raise LedgerError(msg)
        cells[row[0]] = row[4]
    return cells


def _is_teardown_timeout(error: ET.Element) -> bool:
    """Return whether an `<error>` is btclib-org/btclib-node#1274's timeout."""
    return _TEARDOWN_TIMEOUT.match(error.get("message") or "") is not None


def parse_junit(path: Path) -> dict[str, str]:
    """Return `{"classname::name": status}` for every testcase a report holds.

    :param path: the JUnit XML report's own path.
    :returns: `"fail"` where a `<failure>` or an `<error>` child is
        present, `"skip"` where a `<skipped>` one is, `"pass"` otherwise.
        A teardown timeout (`teardown_timeouts`) is not counted as an
        `<error>`: the call's own outcome stands. Pytest writes a second
        `<testcase>` of the same name for a teardown error after a call
        that failed or skipped, so the outcomes of one name are merged
        and the worst stands.
    """
    root = ET.parse(path).getroot()  # noqa: S314
    results: dict[str, str] = {}
    for case in root.iter("testcase"):
        key = f"{case.get('classname')}::{case.get('name')}"
        errors = [e for e in case.findall("error") if not _is_teardown_timeout(e)]
        if case.find("failure") is not None or errors:
            status = "fail"
        elif case.find("skipped") is not None:
            status = "skip"
        else:
            status = "pass"
        results[key] = _outcome([results[key], status]) if key in results else status
    return results


def teardown_timeouts(path: Path) -> list[str]:
    """Return the testcases whose teardown timed out, as `parse_junit` keys.

    :param path: the JUnit XML report's own path.
    :returns: the sorted `classname::name` of each testcase carrying the
        `<error>` of btclib-org/btclib-node#1274.
    """
    root = ET.parse(path).getroot()  # noqa: S314
    return sorted(
        f"{case.get('classname')}::{case.get('name')}"
        for case in root.iter("testcase")
        if any(_is_teardown_timeout(e) for e in case.findall("error"))
    )


def locate(key: str) -> tuple[str, str]:
    """Return a testcase's module stem and its function.

    :param key: `parse_junit`'s own `classname::name`, a parametrized
        name carrying its `[id]`.
    :returns: the module's stem less `_btclib_node_test`, and the
        function's name less any `[id]`.
    """
    classname, name = key.split("::", 1)
    return classname.rsplit(".", 1)[-1].removesuffix(_SUFFIX), name.split("[", 1)[0]


def row_of(stem: str, function: str) -> str | None:
    """Return the ledger row a test function is, from `_ROWS`.

    :param stem: the module's stem less `_btclib_node_test`.
    :param function: the test function's own name.
    :returns: the row's first cell, or None where `_ROWS` names neither
        the function nor its module.
    """
    return _ROWS.get(f"{stem}::{function}", _ROWS.get(stem))


def _outcome(statuses: list[str]) -> str:
    """Return a row's outcome from its testcases' own statuses."""
    if "fail" in statuses:
        return "fail"
    if "skip" in statuses:
        return "skip"
    return "pass"


_KIND = {
    ("pass", "fail"): "regression",
    ("pass", "skip"): "skipped",
    ("fail", "pass"): "fixed",
    ("fail", "skip"): "skipped",
    ("skip", "pass"): "ran",
    ("skip", "fail"): "ran, and failed",
    ("flaky", "skip"): "skipped",
}

# the row outcomes that agree with each expected one
_AGREES = {
    "pass": {"pass"},
    "fail": {"fail"},
    "skip": {"skip"},
    "flaky": {"pass", "fail"},
}


def moves(ledger_text: str, results: dict[str, str], build: str) -> list[str]:
    """Return one report line per row, or testcase, that moved.

    :param ledger_text: `TF2.md`'s own text.
    :param results: `parse_junit`'s reading of the report.
    :param build: `"release"` or `"main"`, the build the report ran.
    :returns: the lines, one per ledger row whose outcome does not
        agree with its cell's verdict for that build, per row no testcase
        of the report reached, and per testcase `_ROWS` has no row for.
    :raises LedgerError: where a cell is in no shape the ledger defines.
    """
    by_row, lines = _by_row(results)
    for row, cell in ledger_cells(ledger_text).items():
        verdict = expected(row, cell, build)
        if verdict is None:
            continue
        cases = by_row.get(row)
        if not cases:
            lines.append(f"- **missing**: {row} -- no testcase of this report is its")
            continue
        outcome = _outcome(list(cases.values()))
        if outcome in _AGREES[verdict.outcome]:
            continue
        names = ", ".join(
            f"`{name}`" for name, status in cases.items() if status == outcome
        )
        lines.append(
            f"- **{_KIND[verdict.outcome, outcome]}**: {row} -- TF2.md reads"
            f" {verdict.text}; this run's outcome is {outcome}: {names}"
        )
    return lines


def flaky_passes(ledger_text: str, results: dict[str, str], build: str) -> list[str]:
    """Return one report line per **flaky** row this run passed.

    :param ledger_text: `TF2.md`'s own text.
    :param results: `parse_junit`'s reading of the report.
    :param build: `"release"` or `"main"`, the build the report ran.
    :returns: the lines, none of them a move.
    :raises LedgerError: where a cell is in no shape the ledger defines.
    """
    by_row = _by_row(results)[0]
    lines: list[str] = []
    for row, cell in ledger_cells(ledger_text).items():
        verdict = expected(row, cell, build)
        if verdict is None or verdict.outcome != "flaky":
            continue
        cases = by_row.get(row)
        if cases and _outcome(list(cases.values())) == "pass":
            lines.append(
                f"- **flaky passed**: {row} -- TF2.md reads {verdict.text}; its"
                " legend says when this moves to pass"
            )
    return lines


def _by_row(results: dict[str, str]) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Group a report's testcases by the ledger row each is.

    :param results: `parse_junit`'s reading of the report.
    :returns: each row's testcases, by function name, against their
        status; and one line per testcase `_ROWS` has no row for.
    """
    by_row: dict[str, dict[str, str]] = {}
    lines: list[str] = []
    for key, status in sorted(results.items()):
        stem, function = locate(key)
        row = row_of(stem, function)
        if row is not None:
            by_row.setdefault(row, {})[key.split("::", 1)[1]] = status
        elif stem not in _NO_ROW:
            lines.append(
                f"- **no row**: `{key}` is a testcase `_ROWS` names no row for"
            )
        elif status == "fail":
            lines.append(f"- **no row**: `{key}` fails, and no ledger row covers it")
    return by_row, lines


def main() -> int:
    """Compare the report with the ledger, and print what moved.

    It exits 1 where anything moved, and 0 where nothing did.
    A usage error is the only way this exits 2, the workflow step passing
    all three arguments every time.
    """
    args = sys.argv[1:]
    if len(args) != 3 or args[2] not in BUILDS:
        print(
            f"usage: {Path(sys.argv[0]).name} <TF2.md> <junit.xml> <release|main>",
            file=sys.stderr,
        )
        return 2
    ledger_path, report_path, build = Path(args[0]), Path(args[1]), args[2]
    ledger_text = ledger_path.read_text(encoding="utf-8")
    results = parse_junit(report_path)
    lines = moves(ledger_text, results, build)
    passes = flaky_passes(ledger_text, results, build)
    timeouts = teardown_timeouts(report_path)
    print(f"### TF2.md's btclib-node column against the {build} build")
    print()
    if lines:
        print("Moved since TF2.md:")
        print()
        for line in lines:
            print(line)
    else:
        print("Nothing moved: every row agrees with TF2.md.")
    if passes:
        print()
        print("### Flaky rows this run passed, not moves")
        print()
        for line in passes:
            print(line)
    if timeouts:
        print()
        print(f"### Teardown timeouts ({_TEARDOWN_ISSUE}), not verdicts")
        print()
        for key in timeouts:
            print(f"- `{key}`: the node ignored terminate; its call's outcome stands")
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
