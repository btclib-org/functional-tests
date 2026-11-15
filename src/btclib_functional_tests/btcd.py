# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BtcdAdapter`: btcd, a full node in Go, on regtest.

The measurements below are of btcd's `v0.26.2` release
(btcsuite/btcd@05585e03), the one `node-integration.yml`'s `btcd` job
installs. This module holds no fetch: it is handed the daemon's path,
and `tests/integration/conftest.py` is what hands it over.

btcd spells an option `--name`, not Core's `-name`, and reads a
single-dash word as a run of short options: `-uacomment=x` sets `-u`,
its `--rpcuser`, to `acomment=x`. So a test's own Core-spelled
`extra_args` must reach btcd only behind a capability, or translated
here: `-whitelist=noban@<address>` becomes `--whitelist=<address>`,
which `config.go` describes as an address "that will not be banned",
and a `-whitelist` naming another permission is refused, btcd having
none.

`Capability.MINE` is `NodeAdapter.mine`, a block built client-side over
`submitblock`: btcd's `rpcserver.go` registers `generate` and neither
`generatetoaddress` nor `generateblock`, so `Capability.GENERATE` is not
declared. `Capability.CONNECT` is `addnode <address> onetry`: btcd's `addnode`
takes two arguments and refuses Core's third, `v2transport` ("wrong
number of params (expected 2, received 3)"), so `dial` below drops it.
`Capability.INVALIDATE_BLOCK`, `Capability.CHAIN_TIPS` and
`Capability.SIGN_MESSAGE_WITH_PRIVKEY` are
`invalidateblock` with `reconsiderblock`, `getchaintips`, and
`signmessagewithprivkey` with `verifymessage`, each in `rpcserver.go`'s
`rpcHandlersBeforeInit`.

Each of these is a capability this release has. README.md's opening
says when a test it fails is a finding.

btcd writes one thing outside its data directory: on every start it
creates its application directory in the user's home
(`os.MkdirAll(defaultHomeDir)`, `config.go`), whatever `--datadir` says,
`btcutil.AppDataDir` finding the home through `user.Current()` before
`HOME`. Under `--regtest` and `--notls` it writes no file there: the
default configuration file is created only off regtest, simnet and
signet (`loadConfig`), and the RPC certificate only without `--notls`
(`server.go`).
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, override

from bitcoin_core_rpc import BitcoinCoreRpcClient

from btclib_functional_tests.capability import Capability
from btclib_functional_tests.node import NodeAdapter
from btclib_functional_tests.timeout_factor import rpc_client_timeout

if TYPE_CHECKING:
    from collections.abc import Set as AbstractSet
    from urllib.request import Request

    from bitcoin_core_rpc.transport import HttpTransport

__all__ = [
    "BtcdAdapter",
]

# the user this adapter starts btcd with where the caller names no
# credential; the password is drawn per instance
_RPC_USER = "tf2"

# the subdirectory of `--datadir` and of `--logdir` btcd writes into on
# regtest: `netName` (`params.go`) of `regressionNetParams`
_CHAIN_DIR = "regtest"

# btcd's single-letter aliases (`short:` in `config.go`'s `config` and
# `serviceOptions`), each to its long name; `version` is the one taking no
# value
_SHORT_OPTIONS = {
    "a": "addpeer",
    "C": "configfile",
    "b": "datadir",
    "d": "debuglevel",
    "P": "rpcpass",
    "u": "rpcuser",
    "V": "version",
    "s": "service",
}


def _check_short_aliases(command: Sequence[str], extra_args: Sequence[str]) -> None:
    """Refuse an `extra_args` entry setting an option of `command` by its alias.

    `NodeAdapter`'s own check reads `-name` and `--name` alone. A
    single-dash entry is a run of btcd's short options, the first that
    takes a value taking the rest of the entry: `-ufoo` sets `--rpcuser`.

    :param command: the adapter's own argv, `_command()`'s answer.
    :param extra_args: what a caller asks to append after it.
    :raises ValueError: an entry sets, by its short alias, an option
        `command` already sets.
    """
    names = {token.lstrip("-").split("=", 1)[0] for token in command}
    for arg in extra_args:
        if not arg.startswith("-") or arg.startswith("--"):
            continue
        for letter in arg[1:]:
            name = _SHORT_OPTIONS.get(letter)
            if name in names:
                err_msg = f"extra_args reuses -{name}, which this adapter sets itself"
                raise ValueError(err_msg)
            if name != "version":
                break


def _spelled_for_btcd(extra_args: Sequence[str]) -> list[str]:
    """Return `extra_args` with `-whitelist=noban@<address>` spelled for btcd.

    In Core's `-whitelist`, `in` and `out` set the direction the entry
    covers, not a permission. btcd's `--whitelist` covers both, so they
    are dropped before the permissions are compared with `noban`.

    :param extra_args: what a caller asks to append to the argv.
    :raises ValueError: a `-whitelist` entry names a permission other
        than `noban`, or names none.
    """
    spelled: list[str] = []
    for arg in extra_args:
        if not arg.startswith("-whitelist="):
            spelled.append(arg)
            continue
        flags, _, address = arg.removeprefix("-whitelist=").rpartition("@")
        permissions = set(flags.split(",")) - {"in", "out"}
        if permissions != {"noban"}:
            err_msg = f"btcd's --whitelist grants no permission but noban: {arg}"
            raise ValueError(err_msg)
        spelled.append(f"--whitelist={address}")
    return spelled


def _one_member(reply: object) -> object:
    """Return `reply` with its null `result` or `error` dropped, where 2.0.

    :param reply: one JSON-RPC reply object, or anything else, returned
        as it is.
    """
    if not isinstance(reply, dict) or reply.get("jsonrpc") != "2.0":
        return reply
    if "result" in reply and "error" in reply:
        null = "error" if reply["error"] is None else "result"
        if reply[null] is None:
            return {key: value for key, value in reply.items() if key != null}
    return reply


def _v2_replies(transport: HttpTransport) -> HttpTransport:
    """Wrap `transport`, giving each 2.0 reply one of `result` and `error`.

    btcd answers a request carrying `"jsonrpc": "2.0"` with both members,
    the unused one null, where JSON-RPC 2.0 has exactly one, and
    `bitcoin_core_rpc` refuses such a reply ("a 2.0 reply with both result
    and error"). A request without the marker gets an empty body. Measured
    against `v0.26.2` with `getblockcount` and an unknown method.

    :param transport: the transport to wrap, `NodeAdapter._rpc_transport`.
    """

    def _translated(request: Request, timeout: float) -> tuple[int, bytes]:
        status, body = transport(request, timeout)
        try:
            reply = json.loads(body)
        except ValueError:
            return status, body
        if isinstance(reply, list):
            return status, json.dumps([_one_member(item) for item in reply]).encode()
        return status, json.dumps(_one_member(reply)).encode()

    return _translated


class BtcdAdapter(NodeAdapter):
    """A `btcd`, over plain HTTP on loopback with a user and password.

    btcd writes no cookie, and serves no RPC without a user and password
    (`config.go`'s `--norpc` help). The credential is `rpc_auth`
    where the caller names one, and otherwise `_RPC_USER` with a password
    drawn for this instance. Either is on `_command`'s argv, so
    `extra_args` cannot name another, by its long name or its short alias.
    """

    capabilities: AbstractSet[Capability] = frozenset(
        {
            Capability.MINE,
            Capability.CONNECT,
            Capability.INVALIDATE_BLOCK,
            Capability.CHAIN_TIPS,
            Capability.SIGN_MESSAGE_WITH_PRIVKEY,
        }
    )
    _discards_stdout = True

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
        """Draw the credential first: `_command` reads it.

        `NodeAdapter.__init__` calls `_command` to check `extra_args`
        before it stores `rpc_auth`.
        """
        self._credential = rpc_auth or (_RPC_USER, secrets.token_hex(16))
        extra_args = _spelled_for_btcd(extra_args)
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
        _check_short_aliases(self._command(), extra_args)

    @override
    def _command(self) -> list[str]:
        """Return btcd's own argv, a loopback-only regtest node.

        `--notls`: btcd serves RPC over TLS by default, which
        `bitcoin_core_rpc` does not speak, and allows it off only where
        every `--rpclisten` is a loopback address (`config.go`).
        `--listen` names the one address btcd accepts peers on. `--logdir`
        puts `btcd.log` beside the chain's data.
        """
        user, password = self._credential
        return [
            self._executable,
            "--regtest",
            f"--datadir={self._datadir}",
            f"--logdir={self._datadir}",
            f"--rpclisten=127.0.0.1:{self._rpc_port}",
            "--notls",
            f"--rpcuser={user}",
            f"--rpcpass={password}",
            f"--listen=127.0.0.1:{self._p2p_port}",
        ]

    @override
    def restart(self, extra_args: Sequence[str] | None = None) -> None:
        """Restart as `NodeAdapter.restart` does, refusing short aliases too.

        :param extra_args: what to append after `_command`'s own argv for
            this start, in place of the constructor's.
        :raises ValueError: an entry of `extra_args` sets an option
            `_command` already sets, or names a `-whitelist` permission
            btcd lacks, before the running node is stopped.
        """
        if extra_args is not None:
            extra_args = _spelled_for_btcd(extra_args)
            _check_short_aliases(self._command(), extra_args)
        super().restart(extra_args)

    @override
    def _rpc_client(self) -> BitcoinCoreRpcClient:
        """Return a client authenticating with the credential btcd started with.

        Built over `NodeAdapter._rpc_transport`, this adapter's own
        connections, `--tracerpc` included, and bounding each call by
        `timeout_factor.rpc_client_timeout`, as the other adapters do.
        """
        user, password = self._credential
        return BitcoinCoreRpcClient(
            f"http://127.0.0.1:{self._rpc_port}",
            user=user,
            password=password,
            timeout=rpc_client_timeout(),
            transport=self._rpc_transport(),
        )

    @override
    def _rpc_transport(self) -> HttpTransport:
        """Return the base transport, its replies through `_v2_replies`.

        Wrapped outside the trace, so `--tracerpc` prints the length of btcd's
        own reply.
        """
        return _v2_replies(super()._rpc_transport())

    @property
    def log_path(self) -> Path:
        """Return this node's own `btcd.log`."""
        return self._datadir / _CHAIN_DIR / "btcd.log"

    @override
    def _log_path(self) -> Path:
        """Return `log_path`, for `start`'s own errors."""
        return self.log_path

    @override
    def dial(self, address: str, *, v2transport: bool) -> None:
        """Have btcd dial `address` once, over its two-argument `addnode`.

        :param address: `host:port` to dial.
        :param v2transport: must be `False`: btcd's `addnode` takes no
            transport, its BIP324 being the node-wide `--v2transport`.
        :raises ValueError: `v2transport` is `True`.
        """
        if v2transport:
            err_msg = "btcd's addnode cannot ask for BIP324: start it --v2transport"
            raise ValueError(err_msg)
        self.rpc.call("addnode", [address, "onetry"])
