# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_users`, one body over each node.

Read from Core's `test/functional/rpc_users.py` (`fad441867c9b`,
2026-10-12), disk-family mechanism alone (issue btclib-org/btclib#2220,
[ISS 7](https://github.com/btclib-org/functional-tests/issues/7)):
`rpcauth`, `rpcuser`/`rpcpassword` and `rpccookieperms` are each written
to the datadir this adapter is given, either through `bitcoin.conf`
before it ever starts or on the command line as `extra_args` -- neither
needs an adapter change, `NodeAdapter.__init__`'s own
`_check_extra_args` raising nothing for any of the three, none of them
an option either adapter's `_command()` sets itself.

A smaller claim than Core's own file, declared rather than silent.
Dropped: the `rpcauth.py` script Core's own harness runs as a subprocess
to generate two of its four credentials, and the `SystemRandom`-chosen
username -- both a claim about that script rather than about
`Capability.RPC_AUTH_CONFIG`, which `_rpcauth_line` below generates a
credential for the same way `rpc_whitelist_test.py` already does.
`-rpcuser`/`-rpcpassword` and `-norpccookiefile` each write no RPC
cookie, measured live against the release `node-integration.yml` pins --
`rpcauth` given alongside either does not bring the cookie back -- so a
client that waits on the cookie the adapter's `_rpc_client()` otherwise
reads never sees one and `start` timed out rather than the node ever
answering RPC
([ISS functional-tests#34](https://github.com/btclib-org/functional-tests/issues/34)).
`rpc_auth` (`NodeAdapter.__init__`, `node.py`) is what lets them back
in: the caller that puts either flag in `extra_args` already knows the
plaintext credential it chose there, exactly as Core's own harness knows
the plaintext of whatever it wrote into `bitcoin.conf`, and passes it to
the adapter instead of leaving the readiness wait on a cookie the node
never writes. Core's own equivalent test hits the identical gap for
`-norpccookiefile` alone and works around it with
`busy_wait_for_debug_log`, an alternate readiness wait keyed on the
debug log rather than RPC -- unneeded here, since `-norpccookiefile`
below is paired with an `-rpcauth` value whose plaintext this file
already holds, unlike Core's own `test_norpccookiefile`, which pairs it
with a `-rpcauth` value `get_auth_cookie` (`test_framework/util.py`) has
no way to recover the plaintext of from `bitcoin.conf` alone. Of Core's
own `test_auth` combinations, the `-rpcuser`/`-rpcpassword` test checks
the right credential and a wrong password only: the wrong user and the
wrong pair go through the same comparison (`CheckUserAuthorized`,
`httprpc.cpp`) that `rpcauth_via_config_authenticates_and_refuses`
already drives through all four. Dropped last: `platform.system() ==
'Windows'` is never this repository's own gate (`CONTRIBUTING.md`'s own
table: one image, `ubuntu-latest`), so the branch `test_rpccookieperms`
takes on it is unreachable here and the POSIX permission check below is
the whole of that test.

Ported, against its own capability rather than `RPC_AUTH_CONFIG`:
Core's own "-norpcauth disables previous -rpcauth params" check,
`Capability.RPC_AUTH_NEGATION` (`capability.py`) -- a fact
`BitcoindAdapter` declares unconditionally and `BtclibNodeAdapter`
declares only on a build its own `_negates_rpcauth` finds reading
`-norpcauth`, so this test's own row on any other build of that node is
a counted skip rather than a narrowed run. Every other test asks
`Capability.RPC_AUTH_CONFIG` before it starts the node, and counts
a skip where the build lacks it: `-rpcauth` and `-rpccookieperms` are
not recognised at all, in `bitcoin.conf` or on the command line, by a
btclib-node build before
[ISS btclib-node#1070](https://github.com/btclib-org/btclib-node/issues/1070).

A malformed `-rpcauth` value's refusal is matched in Core's own wording
alone, `_INIT_ERROR`: `Error: Unable to start HTTP server. See
debug log for details.`, the whole text Core's `rpc_users.py` compares,
anchored past `_wait_for_rpc`'s own `stderr: ` (`node.py`) and at the
message's end, so that a stderr carrying anything beside the refusal
fails it. Every one of the malformed values refuses with that generic
init error on bitcoind and on btclib-node from
[PR btclib-node#1319](https://github.com/btclib-org/btclib-node/pull/1319)
(release `v2026.10.4`) on, as does a cookie the node cannot write:
`__init__.py`'s own `RPC_INIT_ERROR` in btclib-node is that string
verbatim, raised whenever `rpc_manager.start_listener()` fails for any
reason, cookie generation included.

Each `rpc_users_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import base64
import hmac
import http.client
import json
import re
import secrets
from typing import TYPE_CHECKING

import pytest

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import free_ports
from btclib_functional_tests.timeout_factor import scaled

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = [
    "BLANK_RPCAUTH_POSITIONS",
    "COOKIE_PERMISSIONS",
    "MALFORMED_RPCAUTH",
    "blank_rpcauth_refuses_regardless_of_position",
    "cookie_write_failure_aborts_the_node",
    "malformed_rpcauth_refuses_to_start",
    "norpcauth_disables_previous_rpcauth",
    "norpccookiefile_writes_no_cookie_and_rpcauth_still_authenticates",
    "rpcauth_on_command_line_authenticates",
    "rpcauth_via_config_authenticates_and_refuses",
    "rpccookieperms_sets_posix_permission_bits",
    "rpcuser_rpcpassword_authenticates_without_a_cookie",
]

# Core's own `rpc_timeout` (`test_framework.py`), the wait its
# `HTTPConnection` is given, before `--timeout-factor` scales it
_RPC_TIMEOUT = 60

# Core's own malformed `-rpcauth` values, minus the bare `-rpcauth`
# flag with no value at all, dropped: it exercises `argparse`'s own
# "expected one argument" on btclib-node rather than
# `Capability.RPC_AUTH_CONFIG`'s own credential parsing, which the
# other values already cover between them. `-rpcauth=""` reaches each
# node's own argv as the two literal quote characters -- this harness
# passes `extra_args` straight to `subprocess.Popen`, with no shell in
# between to unquote them -- rather than as an empty string; refused all
# the same, no `:` sitting in either character.
MALFORMED_RPCAUTH = (
    "-rpcauth=",
    '-rpcauth=""',
    "-rpcauth=foo",
    "-rpcauth=foo:bar",
    "-rpcauth=foo:bar:baz",
    "-rpcauth=foo$bar:baz",
    "-rpcauth=foo$bar$baz",
)

# Named, well-formed `-rpcauth` entries: pw is "bitcoin" for both,
# Core's own file's own credentials, kept for the same reason
# `_rpcauth_line`'s own values are made up instead -- only their shape
# matters here, not what they authenticate.
_RPCAUTH_USER1 = (
    "-rpcauth=user1:6dd184e5e69271fdd69103464630014f"
    "$eb3d7ce67c4d1ff3564270519b03b636c0291012692a5fa3dd1d2075daedd07b"
)
_RPCAUTH_USER2 = (
    "-rpcauth=user2:57b2f77c919eece63cfa46c2f06e46ae"
    "$266b63902f99f97eeaab882d4a87f8667ab84435c3799f2ce042ef5a994d620b"
)

# a blank `-rpcauth=` first, between and last among the named entries
BLANK_RPCAUTH_POSITIONS = (
    (_RPCAUTH_USER1, _RPCAUTH_USER2, "-rpcauth="),
    (_RPCAUTH_USER1, "-rpcauth=", _RPCAUTH_USER2),
    ("-rpcauth=", _RPCAUTH_USER1, _RPCAUTH_USER2),
)

# `-rpccookieperms`' value (`None` for unset) and the cookie's mode
COOKIE_PERMISSIONS = (
    (None, 0o600),
    ("owner", 0o600),
    ("group", 0o640),
    ("all", 0o644),
)

# The module docstring has why the whole stderr is matched.
_INIT_ERROR = (
    "stderr: "
    + re.escape("Error: Unable to start HTTP server. See debug log for details.")
    + r"\Z"
)


def _rpcauth_line(username: str, password: str) -> str:
    """Return `username:salt$hash`, Core's own `rpcauth.py` own hash.

    :param username: the RPC user this line authenticates.
    :param password: that user's own plaintext password.
    """
    salt = secrets.token_hex(16)
    digest = hmac.new(salt.encode(), password.encode(), "sha256").hexdigest()
    return f"{username}:{salt}${digest}"


def _call(port: int, user: str, password: str, method: str) -> int:
    """Return the HTTP status `method` answers with, under basic auth.

    :param port: the node's own RPC port.
    :param user: the RPC username to authenticate as.
    :param password: that user's own plaintext password.
    :param method: the RPC method named, with no parameters.
    """
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    headers = {"Authorization": f"Basic {credentials}"}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=scaled(_RPC_TIMEOUT))
    try:
        conn.request("POST", "/", json.dumps({"method": method}), headers)
        return conn.getresponse().status
    finally:
        conn.close()


def rpcauth_via_config_authenticates_and_refuses(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check a `bitcoin.conf` `rpcauth` credential passes; others answer 401.

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
        f"rpcauth={_rpcauth_line('alice', 'alicepw')}\n"
    )
    adapter.start()
    try:
        assert _call(rpc_port, "alice", "alicepw", "getbestblockhash") == 200
        assert _call(rpc_port, "alice", "wrongpw", "getbestblockhash") == 401
        assert _call(rpc_port, "eve", "alicepw", "getbestblockhash") == 401
        assert _call(rpc_port, "eve", "wrongpw", "getbestblockhash") == 401
    finally:
        adapter.stop()


def rpcauth_on_command_line_authenticates(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check the same credential, given as `extra_args`, authenticates too.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        cls,
        executable,
        tmp_path / "datadir",
        rpc_port,
        p2p_port,
        extra_args=(f"-rpcauth={_rpcauth_line('alice', 'alicepw')}",),
    )
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    adapter.start()
    try:
        assert _call(rpc_port, "alice", "alicepw", "getbestblockhash") == 200
        assert _call(rpc_port, "alice", "wrongpw", "getbestblockhash") == 401
    finally:
        adapter.stop()


def malformed_rpcauth_refuses_to_start(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
    rpcauth: str,
) -> None:
    """Check a malformed `-rpcauth` value is fatal at startup.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    :param rpcauth: one of `MALFORMED_RPCAUTH`.
    """
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        cls,
        executable,
        tmp_path / "datadir",
        rpc_port,
        p2p_port,
        extra_args=(rpcauth,),
    )
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    with pytest.raises(RuntimeError, match=_INIT_ERROR):
        adapter.start()


def blank_rpcauth_refuses_regardless_of_position(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
    extra_args: tuple[str, ...],
) -> None:
    """Check a blank `-rpcauth=` is fatal wherever it sits among named entries.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    :param extra_args: one of `BLANK_RPCAUTH_POSITIONS`.
    """
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        cls,
        executable,
        tmp_path / "datadir",
        rpc_port,
        p2p_port,
        extra_args=extra_args,
    )
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    with pytest.raises(RuntimeError, match=_INIT_ERROR):
        adapter.start()


def norpcauth_disables_previous_rpcauth(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check `-norpcauth` disables every `-rpcauth` value given before it.

    Skipped where the adapter does not declare `Capability.RPC_AUTH_NEGATION`.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        cls,
        executable,
        tmp_path / "datadir",
        rpc_port,
        p2p_port,
        extra_args=(_RPCAUTH_USER1, _RPCAUTH_USER2, "-norpcauth"),
    )
    require(Capability.RPC_AUTH_NEGATION, adapter.capabilities, skip_counts)
    adapter.start()
    try:
        assert _call(rpc_port, "user1", "bitcoin", "getbestblockhash") == 401
        assert _call(rpc_port, "user2", "bitcoin", "getbestblockhash") == 401
    finally:
        adapter.stop()


def rpcuser_rpcpassword_authenticates_without_a_cookie(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check `-rpcuser`/`-rpcpassword` authenticates; no cookie is ever written.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    datadir = tmp_path / "datadir"
    rpc_port, p2p_port = free_ports(2)
    probe = make_adapter(cls, executable, tmp_path / "probe", rpc_port, p2p_port)
    require(Capability.RPC_AUTH_CONFIG, probe.capabilities, skip_counts)
    adapter = make_adapter(
        cls,
        executable,
        datadir,
        rpc_port,
        p2p_port,
        extra_args=("-rpcuser=bob", "-rpcpassword=bobpw"),
        rpc_auth=("bob", "bobpw"),
    )
    adapter.start()
    try:
        assert _call(rpc_port, "bob", "bobpw", "getbestblockhash") == 200
        assert _call(rpc_port, "bob", "wrongpw", "getbestblockhash") == 401
        assert not (datadir / "regtest" / ".cookie").exists()
    finally:
        adapter.stop()


def norpccookiefile_writes_no_cookie_and_rpcauth_still_authenticates(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check `-norpccookiefile` writes no cookie; `-rpcauth` still works.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    datadir = tmp_path / "datadir"
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        cls,
        executable,
        datadir,
        rpc_port,
        p2p_port,
        extra_args=(_RPCAUTH_USER1, "-norpccookiefile"),
        rpc_auth=("user1", "bitcoin"),
    )
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    adapter.start()
    try:
        assert _call(rpc_port, "user1", "bitcoin", "getbestblockhash") == 200
        assert _call(rpc_port, "user1", "wrongpw", "getbestblockhash") == 401
        assert not (datadir / "regtest" / ".cookie").exists()
    finally:
        adapter.stop()


def rpccookieperms_sets_posix_permission_bits(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
    perm: str | None,
    expected_mode: int,
) -> None:
    """Check `-rpccookieperms` sets the cookie's mode; unset means owner.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    :param perm: the option's value, or `None` to leave it unset.
    :param expected_mode: the mode the cookie then has.
    """
    datadir = tmp_path / "datadir"
    extra_args = (f"-rpccookieperms={perm}",) if perm else ()
    rpc_port, p2p_port = free_ports(2)
    adapter = make_adapter(
        cls,
        executable,
        datadir,
        rpc_port,
        p2p_port,
        extra_args=extra_args,
    )
    require(Capability.RPC_AUTH_CONFIG, adapter.capabilities, skip_counts)
    adapter.start()
    try:
        cookie_path = datadir / "regtest" / ".cookie"
        actual_mode = cookie_path.stat().st_mode & 0o777
        assert actual_mode == expected_mode
    finally:
        adapter.stop()


def cookie_write_failure_aborts_the_node(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check a directory sitting at the cookie path is a fatal init error.

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
    cookie_dir = datadir / "regtest"
    cookie_dir.mkdir(parents=True)
    (cookie_dir / ".cookie.tmp").mkdir()
    with pytest.raises(RuntimeError, match=_INIT_ERROR):
        adapter.start()
