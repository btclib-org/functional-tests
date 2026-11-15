# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

r"""Core's `rpc_whitelist`, as bodies over each node.

Read from Core's `test/functional/rpc_whitelist.py` (`fad441867c9b`,
2026-10-12), disk-family mechanism alone (issue btclib-org/btclib#2220,
[ISS 7](https://github.com/btclib-org/functional-tests/issues/7)):
`rpcauth`, `rpcwhitelist` and `rpcwhitelistdefault` are `bitcoin.conf`
keys, written to the datadir this adapter is given before it ever
starts -- no `extra_args` and no adapter change, `bitcoin.conf` living
at the same path a caller already holds (`datadir_path`'s own
constructor argument).

A smaller claim than Core's own file, declared rather than silent: two
users rather than Core's `strange_users` roster of malformed-input
edge cases, which asks what a hand-written parser does with a blank or
a doubled permission list rather than what `rpcwhitelist` restricts --
a claim about this project's own config parser, not about the
mechanism `Capability.RPC_AUTH_CONFIG` names. The `rpcauth` credential
below is generated the way Core's own `share/rpcauth/rpcauth.py`
generates one (`salt$hmac_sha256(salt, password)`), read from that
script rather than run: this repository ships no copy of it.

`Capability.RPC_AUTH_CONFIG` is asked before `bitcoin.conf` is written,
so a build without it skips before the data directory exists.
`btclib_node.py`'s own docstring is where that capability's
per-executable probe is argued.

Core's `test_rejected_method_logging` is a body of its own, asking for
`Capability.DEBUG_LOG` besides: a method the whitelist refuses is logged
as named, sent alone or in a batch, whether it carries a newline that
could forge a log line or every printable special character. A bitcoind
escapes the newline as `\x0a` from bitcoin/bitcoin#35833, which no tag
up to `v32.1` carries, and writes it raw before; which of the two is
asserted is read off the build's own `getnetworkinfo` `version`
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)).

Each `rpc_whitelist_<node>_test.py` module beside this one runs each body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import base64
import hmac
import http.client
import json
import secrets
from typing import TYPE_CHECKING

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import free_ports
from btclib_functional_tests.timeout_factor import scaled
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = [
    "a_rejected_method_is_logged_as_named",
    "rpcwhitelist_restricts_a_users_own_rpc_surface",
    "rpcwhitelistdefault_governs_an_unlisted_user",
]

# Core's own `rpc_timeout` (`test_framework.py`), the wait its
# `HTTPConnection` is given, before `--timeout-factor` scales it
_RPC_TIMEOUT = 60

# Core's own line a forged method name carries after a newline
_FORGED_LOG_LINE = (
    "ERROR: ConnectTip: ConnectBlock 0000000000000000deadbeefdeadbeefdeadbeef"
    "deadbeefdeadbeefdeadbeef failed, bad-txns-inputs-missingorspent"
)

# Core's own rejected methods: one forging a log line, one carrying every
# printable special character
_REJECTED_METHODS = (
    f"getblock\n{_FORGED_LOG_LINE}",
    "getblock!\"#$%&'*+<=>[\\]^`{|}~",
)

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running
# build's own `getnetworkinfo` `version`, at or past which the log
# escapes a newline: `32.99`'s, no tag up to `v32.1` carrying
# bitcoin/bitcoin#35833. A known limit: a `master` build from the
# version's move to `32.99` (`f3fec67c3e`, 2026-09-11) until that
# change's merge (`d4b0e1e4b0`, 2026-09-30) reports `329900` and writes
# the newline raw, so this test fails against such a build
_ESCAPES_NEWLINE_VERSION = 329900


def _rpcauth_line(username: str, password: str) -> str:
    """Return `username:salt$hash`, Core's own `rpcauth.py` own hash.

    :param username: the RPC user this line authenticates.
    :param password: that user's own plaintext password.
    """
    salt = secrets.token_hex(16)
    digest = hmac.new(salt.encode(), password.encode(), "sha256").hexdigest()
    return f"{username}:{salt}${digest}"


def _call(
    port: int, user: str, password: str, method: str, *, batch: bool = False
) -> int:
    """Return the HTTP status `method` answers with, under basic auth.

    :param port: the node's own RPC port.
    :param user: the RPC username to authenticate as.
    :param password: that user's own plaintext password.
    :param method: the RPC method named, with no parameters.
    :param batch: whether the request is a batch holding that call alone.
    """
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {credentials}"}
    request = {"method": method}
    body = json.dumps([request] if batch else request)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=scaled(_RPC_TIMEOUT))
    try:
        conn.request("POST", "/", body, headers)
        return conn.getresponse().status
    finally:
        conn.close()


def rpcwhitelist_restricts_a_users_own_rpc_surface(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check a whitelisted user reaches only its own methods; others answer 403.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    datadir = tmp_path / "datadir"
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(cls, executable, datadir, rpc_port, p2p_port)
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    datadir.mkdir()
    (datadir / "bitcoin.conf").write_text(
        "rpcwhitelistdefault=0\n"
        f"rpcauth={_rpcauth_line('alice', 'alicepw')}\n"
        "rpcwhitelist=alice:getbestblockhash,getblockcount\n"
        f"rpcauth={_rpcauth_line('bob', 'bobpw')}\n"
        "rpcwhitelist=bob:getblockcount\n"
    )
    adapter.start()
    try:
        assert _call(rpc_port, "alice", "alicepw", "getbestblockhash") == 200
        assert _call(rpc_port, "alice", "alicepw", "getblockcount") == 200
        assert _call(rpc_port, "alice", "alicepw", "getblockchaininfo") == 403
        assert _call(rpc_port, "bob", "bobpw", "getblockcount") == 200
        assert _call(rpc_port, "bob", "bobpw", "getbestblockhash") == 403
        assert _call(rpc_port, "alice", "wrong", "getblockcount") == 401
    finally:
        adapter.stop()


def rpcwhitelistdefault_governs_an_unlisted_user(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check `rpcwhitelistdefault=1` refuses a user `rpcwhitelist` never named.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    datadir = tmp_path / "datadir"
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(cls, executable, datadir, rpc_port, p2p_port)
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    datadir.mkdir()
    (datadir / "bitcoin.conf").write_text(
        "rpcwhitelistdefault=1\n"
        f"rpcauth={_rpcauth_line('carol', 'carolpw')}\n"
        # `NodeAdapter.start` polls `getblockchaininfo` over the cookie this
        # adapter authenticates with (`node.py`'s own `_wait_for_rpc`), and
        # `rpcwhitelistdefault=1` restricts `__cookie__` exactly as it
        # restricts any other user -- Core's own test whitelists it too,
        # for the same reason, once it turns this setting on.
        "rpcwhitelist=__cookie__:getblockchaininfo\n"
    )
    adapter.start()
    try:
        assert _call(rpc_port, "carol", "carolpw", "getbestblockhash") == 403
    finally:
        adapter.stop()


def a_rejected_method_is_logged_as_named(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check a refused method is logged as named, a newline escaped or not.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    :raises TypeError: the node declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    datadir = tmp_path / "datadir"
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(cls, executable, datadir, rpc_port, p2p_port)
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    require(Capability.DEBUG_LOG, adapter.capabilities, skip_counts)
    if not isinstance(adapter, BitcoindAdapter):
        err_msg = f"{type(adapter).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    datadir.mkdir()
    (datadir / "bitcoin.conf").write_text(
        "rpcwhitelistdefault=0\n"
        f"rpcauth={_rpcauth_line('alice', 'alicepw')}\n"
        "rpcwhitelist=alice:getbestblockhash,getblockcount\n"
    )
    adapter.start()
    try:
        version = bitcoind_version(adapter)
        escapes = version is None or version >= _ESCAPES_NEWLINE_VERSION
        for method in _REJECTED_METHODS:
            logged = method.replace("\n", "\\x0a") if escapes else method
            for batch in (False, True):
                with assert_debug_log(
                    adapter.debug_log_path, [f"not allowed to call method {logged}"]
                ):
                    status = _call(rpc_port, "alice", "alicepw", method, batch=batch)
                    assert status == 403
    finally:
        adapter.stop()
