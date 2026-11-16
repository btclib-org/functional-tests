# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_net`, the steps that need one node, one body per step.

Read from Core's `test/functional/rpc_net.py` (`332c3e8e3e17`,
2026-10-01), a file of
[ISS 47](https://github.com/btclib-org/functional-tests/issues/47):
`addnode` and `getaddednodeinfo`, the service names of a peer,
`getnodeaddresses`, `addpeeraddress`, `getaddrmaninfo` and
`getrawaddrman`. The steps that connect Core's two nodes
(`test_connection_count`, `test_getpeerinfo`, `test_getnettotals`,
`test_getnetworkinfo` and `test_sendmsgtopeer`) are not ported: they are
[ISS 14](https://github.com/btclib-org/functional-tests/issues/14)'s.

Core's `set_test_params` gives its nodes `-proxy=127.0.0.1:1`, so that
no step dials a public address, `addnode`'s `11.22.33.44` and the
addresses `addpeeraddress` adds among them. Nothing listens there and no
step reads what the node asks of it, so no proxy runs. A step given the
argument asks for `Capability.PROXY`. The steps Core restarts with
`-cjdnsreachable` ask for `Capability.CJDNS`, those that call
`addpeeraddress` for `Capability.KNOWN_ADDRESSES`, the one that sets a
clock for `Capability.CLOCK`, and `addnode` is `Capability.CONNECT`. The
steps whose node starts with `-test=addrman` ask for
`Capability.ADDRESS_TABLES`, for that option, the `-checkaddrman` the
`addpeeraddress` step adds to it, and the `getrawaddrman` and
`getaddrmaninfo` they read
([ISS 555](https://github.com/btclib-org/functional-tests/issues/555)).

Each step runs on a node of its own, started with the arguments Core's
node has at that step, where Core runs the steps in turn on two nodes.
Core's `write_config` (`test_framework/util.py`) also writes `connect=0`
and `dnsseed=0` into every node's `bitcoin.conf`, so both join every
start here. `-minrelaytxfee`, which Core gives its nodes, is not passed:
no step ported reads it.

Core's `test_getrawaddrman` sets the node's clock to the time it then
asserts, so it passes with the clock left alone. Here the clock is set a
day back, and a time read off the wall clock fails.

Core's `test_addpeeraddress` makes its type and range checks on its first
node and the rest on its second; here one node answers all of them. Its
`test_service_flags` is a `v1` connection here, so it asserts the names
of a `v1` peer's services.

Core's blank-address checks differ between builds. In each the step asks
the build a call that changes nothing and asserts what that build answers:

- `addnode` refuses a blank node address with `Node address cannot be
  empty` (bitcoin/bitcoin@90ce21e21d09, 2026-07-27, first in `v32.0rc1`).
  A blank `remove` tells the builds apart, answering `Node could not be
  removed` on a build without the check. The step then asserts that
  answer in place of Core's blank-address calls.
- `addpeeraddress` refuses a blank or non-IP address with `Invalid IP
  address` and code `-30` (bitcoin/bitcoin@316a0c513278, 2025-09-18,
  first in `v31.0rc1`). A build without it answers `{"success": False}`
  to the blank address, which the step asserts, as Core's file does at
  `v30.3`; that file checks no non-IP name.

`getnodeaddresses` refuses `tor` from `v31.0` on (bitcoin/bitcoin#34031),
where an older build reads it as `onion`. That refusal is asserted only of a
build whose `getnetworkinfo` `version` reads `v31.0` or later
([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)), or
of a node other than bitcoind.

`p2p_port(2)` (`test_framework/util.py`), the address `addnode` adds, is
a port of `node.free_ports` here, and Core's `P2P_SERVICES`
(`test_framework/p2p.py`) is `NODE_NETWORK | NODE_WITNESS`.

Each `rpc_net_<node>_test.py` module beside this one runs each body,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import contextlib
import time
from typing import TYPE_CHECKING

import pytest
from bitcoin_core_rpc import RpcError, magic_from_chain
from btclib.p2p import ServiceFlags

from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.node import free_ports
from btclib_functional_tests.peer import Peer
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = [
    "a_node_is_added_listed_and_removed",
    "a_peer_s_service_flags_are_named",
    "addpeeraddress_fills_the_address_tables",
    "getaddrmaninfo_counts_the_addresses_of_each_network",
    "getnodeaddresses_answers_from_the_address_table",
    "getrawaddrman_lists_the_address_tables",
]

# Core's `UNREACHABLE_PROXY_ARG` (`test_framework/netutil.py`)
_UNREACHABLE_PROXY = "-proxy=127.0.0.1:1"

# `write_config`'s own `connect=0` and `dnsseed=0` lines
# (`test_framework/util.py`)
_CONFIG = ("-connect=0", "-dnsseed=0")

# the arguments of Core's node at `set_test_params`, but its minimum relay fee
_PROXIED = (_UNREACHABLE_PROXY, *_CONFIG)

# the arguments of the node Core restarts for the address table steps
_ADDRMAN = ("-test=addrman", *_CONFIG)

_CJDNS_ADDRMAN = ("-cjdnsreachable", *_ADDRMAN)

# Core's `P2P_SERVICES` (`test_framework/p2p.py`)
_P2P_SERVICES = int(ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS)

_BLANK_ADDRESS_REFUSED = "Node address cannot be empty"
_BLANK_REMOVE_REFUSED = "Node could not be removed"
_INVALID_IP = "Invalid IP address"

# the RPC's `RPC_CLIENT_NODE_ALREADY_ADDED` and
# `RPC_CLIENT_NODE_NOT_ADDED` (`src/rpc/protocol.h`)
_NODE_ALREADY_ADDED = -23
_NODE_NOT_ADDED = -24

# `RPC_INVALID_PARAMETER`, `RPC_MISC_ERROR`, `RPC_TYPE_ERROR` and
# `RPC_CLIENT_INVALID_IP_OR_SUBNET`
_INVALID_PARAMETER = -8
_MISC_ERROR = -1
_TYPE_ERROR = -3
_INVALID_IP_OR_SUBNET = -30

# the number of addresses Core's `test_getnodeaddresses` adds
_ADDRESS_COUNT = 10000

# how many calls one batch carries
_BATCH = 1000

# the seconds of a day
_A_DAY = 86400

# `1st June 2018`, the earliest time `getnodeaddresses` may report
_JUNE_2018 = 1527811200

# Core's own `CLIENT_VERSION` (`src/clientversion.h`), the running build's
# own `getnetworkinfo` `version`, at or past which `getnodeaddresses`
# refuses `tor`: `v31.0`'s, `v31.0rc1` being the first tag carrying
# bitcoin/bitcoin#34031. A known limit: a `master` build from its merge
# (`56ce78d5f6`, 2025-12-10) until the version moved to `31.99`
# (`48b952cbb6`, 2026-03-06) reports `309900` and refuses it all the same,
# so the `tor` check is not made against such a build
_TOR_REFUSED_VERSION = 310000


@contextlib.contextmanager
def _node(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
    extra_args: Sequence[str],
    capabilities: Sequence[Capability],
) -> Iterator[NodeAdapter]:
    """Start a node given `extra_args`, and stop it on leaving.

    :param capabilities: what the step needs, each asked of the node
        before it starts.
    """
    rpc_port, p2p_port = free_ports(2)
    node = make_adapter(
        cls, executable, tmp_path / "node", rpc_port, p2p_port, extra_args
    )
    for capability in capabilities:
        require(capability, node.capabilities, skip_counts)
    node.start()
    try:
        yield node
    finally:
        node.stop()


def _refusal(node: NodeAdapter, method: str, params: object) -> RpcError:
    """Return the error `method` answers `params` with.

    :raises AssertionError: the call succeeds.
    """
    with pytest.raises(RpcError) as refused:
        node.rpc.call(method, params)  # type: ignore[arg-type]
    return refused.value


def _assert_refused(
    node: NodeAdapter, code: int, message: str, method: str, params: object
) -> None:
    """Core's `assert_raises_rpc_error`: the code, and a part of the message."""
    refused = _refusal(node, method, params)
    assert refused.code == code
    assert message in str(refused)


def _is_hidden(node: NodeAdapter, method: str) -> None:
    """Check `help` leaves `method` out of its list and still describes it."""
    assert method not in node.rpc.call("help")
    assert f"unknown command: {method}" not in node.rpc.call("help", [method])


def _seed_addrman(node: NodeAdapter) -> None:
    """Core's `seed_addrman`: addresses of five networks, in both tables."""
    onion_1 = "pg6mmjiyjmcrsslvykfwnntlaru7p5svn6y2ymmju6nubxndf4pscryd.onion"
    onion_2 = "nrfj6inpyf73gpkyool35hcmne5zwfmse3jl3aw23vk7chdemalyaqad.onion"
    i2p = "c4gfnttsuwqomiygupdqqqyy5y5emnk5c73hrfvatri67prd7vyq.b32.i2p"
    for address, port, tried in (
        ("1.2.3.4", 8333, True),
        ("2.0.0.0", 8333, False),
        ("1233:3432:2434:2343:3234:2345:6546:4534", 8333, True),
        ("2803:0:1234:abcd::1", 45324, False),
        ("fc00:1:2:3:4:5:6:7", 8333, False),
        (onion_1, 8333, True),
        (onion_2, 45324, True),
        (i2p, 8333, False),
    ):
        assert node.rpc.call(
            "addpeeraddress", {"address": address, "tried": tried, "port": port}
        ) == {"success": True}


def a_node_is_added_listed_and_removed(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Core's `test_addnode_getaddednodeinfo`.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    capabilities = (Capability.PROXY, Capability.CONNECT)
    with _node(
        make_adapter, cls, executable, tmp_path, skip_counts, _PROXIED, capabilities
    ) as node:
        rpc = node.rpc
        assert rpc.call("getaddednodeinfo") == []
        ip_port = f"127.0.0.1:{free_ports(1)[0]}"
        rpc.call("addnode", {"node": ip_port, "command": "add"})

        # an equivalent address, in the shorthand notation
        equivalent = ip_port.replace("127.0.0.1", "127.1")
        _assert_refused(
            node,
            _NODE_ALREADY_ADDED,
            "Node already added",
            "addnode",
            {"node": equivalent, "command": "add"},
        )

        added_nodes = rpc.call("getaddednodeinfo")
        assert len(added_nodes) == 1
        assert added_nodes[0]["addednode"] == ip_port

        # filtering by node
        rpc.call("addnode", {"node": "11.22.33.44", "command": "add"})
        assert rpc.call("getaddednodeinfo", {"node": ip_port}) == added_nodes
        assert len(rpc.call("getaddednodeinfo")) == 2

        _assert_refused(
            node,
            _NODE_ALREADY_ADDED,
            "Node already added",
            "addnode",
            {"node": ip_port, "command": "add"},
        )

        rpc.call("addnode", {"node": ip_port, "command": "remove"})
        added_nodes = rpc.call("getaddednodeinfo")
        assert len(added_nodes) == 1
        assert added_nodes[0]["addednode"] == "11.22.33.44"

        _assert_refused(
            node,
            _MISC_ERROR,
            'addnode "node" "command"',
            "addnode",
            {"node": ip_port, "command": "abc"},
        )

        _blank_addresses(node)
        assert len(rpc.call("getaddednodeinfo")) == 1

        _assert_refused(
            node,
            _NODE_NOT_ADDED,
            "Node could not be removed",
            "addnode",
            {"node": ip_port, "command": "remove"},
        )
        _assert_refused(
            node,
            _NODE_NOT_ADDED,
            "Node has not been added",
            "getaddednodeinfo",
            ["1.1.1.1"],
        )


def _blank_addresses(node: NodeAdapter) -> None:
    """Core's blank node address checks, on a build that has them.

    A blank `remove` changes nothing, so it tells the builds apart: it is
    refused for the blank address on a build with the check and for a node
    not added on one without.
    """
    probe = _refusal(node, "addnode", {"node": " ", "command": "remove"})
    if probe.code == _INVALID_PARAMETER:
        for command in ("add", "remove", "onetry"):
            for blank in ("", " "):
                _assert_refused(
                    node,
                    _INVALID_PARAMETER,
                    _BLANK_ADDRESS_REFUSED,
                    "addnode",
                    {"node": blank, "command": command},
                )
    else:
        assert probe.code == _NODE_NOT_ADDED
        assert _BLANK_REMOVE_REFUSED in str(probe)


def a_peer_s_service_flags_are_named(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Core's `test_service_flags`: unknown service bits are named by position.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    with (
        _node(
            make_adapter,
            cls,
            executable,
            tmp_path,
            skip_counts,
            _PROXIED,
            (Capability.PROXY,),
        ) as node,
        Peer(node.p2p_address, magic_from_chain(node.chain)) as peer,
    ):
        peer.handshake(services=ServiceFlags((1 << 4) | (1 << 63)))
        peers = node.rpc.call("getpeerinfo")
        assert peers[-1]["servicesnames"] == ["UNKNOWN[2^4]", "UNKNOWN[2^63]"]


def getnodeaddresses_answers_from_the_address_table(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Core's `test_getnodeaddresses`.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    capabilities = (Capability.PROXY, Capability.KNOWN_ADDRESSES)
    with (
        _node(
            make_adapter,
            cls,
            executable,
            tmp_path,
            skip_counts,
            _PROXIED,
            capabilities,
        ) as node,
        Peer(node.p2p_address, magic_from_chain(node.chain)) as peer,
    ):
        peer.handshake()
        rpc = node.rpc

        ipv6_addr = "1233:3432:2434:2343:3234:2345:6546:4534"
        rpc.call("addpeeraddress", {"address": ipv6_addr, "port": 8333})

        # Some of these collide, by the way the table places an address.
        imported_addrs = {f"{i >> 8}.{i % 256}.1.1" for i in range(_ADDRESS_COUNT)}
        calls = [
            ("addpeeraddress", [address, 8333]) for address in sorted(imported_addrs)
        ]
        for start in range(0, len(calls), _BATCH):
            rpc.call_batch(calls[start : start + _BATCH])

        assert len(rpc.call("getnodeaddresses")) == 1
        assert len(rpc.call("getnodeaddresses", {"count": 2})) == 2
        assert len(rpc.call("getnodeaddresses", {"network": "ipv4", "count": 8})) == 8

        node_addresses = rpc.call("getnodeaddresses", [0, "ipv4"])
        assert 5000 < len(node_addresses) < _ADDRESS_COUNT
        for address in node_addresses:
            assert address["time"] > _JUNE_2018
            assert address["services"] == _P2P_SERVICES
            assert address["address"] in imported_addrs
            assert address["port"] == 8333
            assert address["network"] == "ipv4"

        ipv6 = rpc.call("getnodeaddresses", [0, "ipv6"])
        assert len(ipv6) == 1
        assert ipv6[0]["address"] == ipv6_addr
        assert ipv6[0]["network"] == "ipv6"
        assert ipv6[0]["port"] == 8333
        assert ipv6[0]["services"] == _P2P_SERVICES

        for network in ("onion", "OnIoN", "i2p", "cjdns"):
            assert rpc.call("getnodeaddresses", [0, network]) == []

        _assert_refused(
            node,
            _INVALID_PARAMETER,
            "Address count out of range",
            "getnodeaddresses",
            [-1],
        )
        version = bitcoind_version(node)
        refuses_tor = version is None or version >= _TOR_REFUSED_VERSION
        for network in ("Foo", "tor") if refuses_tor else ("Foo",):
            _assert_refused(
                node,
                _INVALID_PARAMETER,
                f"Network not recognized: {network}",
                "getnodeaddresses",
                [1, network],
            )


def addpeeraddress_fills_the_address_tables(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Core's `test_addpeeraddress`.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    extra_args = ("-checkaddrman=1", *_ADDRMAN)
    with _node(
        make_adapter,
        cls,
        executable,
        tmp_path,
        skip_counts,
        extra_args,
        (Capability.KNOWN_ADDRESSES, Capability.ADDRESS_TABLES),
    ) as node:
        rpc = node.rpc
        _is_hidden(node, "addpeeraddress")

        _blank_peer_addresses(node)
        assert rpc.call("getnodeaddresses", {"count": 0}) == []

        _assert_refused(
            node,
            _TYPE_ERROR,
            "JSON value of type string is not of expected type bool",
            "addpeeraddress",
            {"address": "1.2.3.4", "tried": "True", "port": 1234},
        )

        for port in (-1, 65536):
            _assert_refused(
                node,
                _MISC_ERROR,
                "JSON integer out of range",
                "addpeeraddress",
                {"address": "1.2.3.4", "port": port},
            )

        # an address in the new table
        success = {"success": True}
        assert (
            rpc.call(
                "addpeeraddress", {"address": "1.0.0.0", "tried": False, "port": 8333}
            )
            == success
        )
        addrman = rpc.call("getrawaddrman")
        assert len(addrman["tried"]) == 0
        new_table = list(addrman["new"].values())
        assert len(new_table) == 1
        assert new_table[0]["address"] == "1.0.0.0"
        assert new_table[0]["port"] == 8333

        # one already there is added to neither table
        already_new = {"success": False, "error": "failed-adding-to-new"}
        for tried in (True, False):
            assert (
                rpc.call(
                    "addpeeraddress",
                    {"address": "1.0.0.0", "tried": tried, "port": 8333},
                )
                == already_new
            )
        assert len(rpc.call("getnodeaddresses", {"count": 0})) == 1

        # an address in the tried table
        assert (
            rpc.call(
                "addpeeraddress", {"address": "1.2.3.4", "tried": True, "port": 8333}
            )
            == success
        )
        addrman = rpc.call("getrawaddrman")
        assert len(addrman["new"]) == 1
        tried_table = list(addrman["tried"].values())
        assert len(tried_table) == 1
        assert tried_table[0]["address"] == "1.2.3.4"
        assert tried_table[0]["port"] == 8333
        # `getnodeaddresses` re-runs the table's checks
        rpc.call("getnodeaddresses", {"count": 0})

        for tried in (True, False):
            assert (
                rpc.call(
                    "addpeeraddress",
                    {"address": "1.2.3.4", "tried": tried, "port": 8333},
                )
                == already_new
            )
        assert len(rpc.call("getnodeaddresses", {"count": 0})) == 2

        # A colliding address, ground out of the table's own placement,
        # stays in the new table when it cannot move to the tried one.
        assert rpc.call(
            "addpeeraddress", {"address": "1.2.5.45", "tried": True, "port": 8333}
        ) == {"success": False, "error": "failed-adding-to-tried"}
        counts = rpc.call("getaddrmaninfo")["all_networks"]
        assert counts["tried"] == 1
        assert counts["new"] == 2

        assert (
            rpc.call("addpeeraddress", {"address": "2.0.0.0", "port": 8333}) == success
        )
        counts = rpc.call("getaddrmaninfo")["all_networks"]
        assert counts["tried"] == 1
        assert counts["new"] == 3
        rpc.call("getnodeaddresses", {"count": 0})


def _blank_peer_addresses(node: NodeAdapter) -> None:
    """Core's refusal of a blank address and of a name, on a build that has it.

    The blank address is asked first: a build with the refusal answers it
    with `Invalid IP address`, and one without answers `{"success": False}`.
    """
    try:
        answer = node.rpc.call("addpeeraddress", {"address": "", "port": 8333})
    except RpcError as refused:
        answer = refused
    if isinstance(answer, RpcError):
        assert answer.code == _INVALID_IP_OR_SUBNET
        assert _INVALID_IP in str(answer)
        # a name: no lookup is made
        _assert_refused(
            node,
            _INVALID_IP_OR_SUBNET,
            _INVALID_IP,
            "addpeeraddress",
            {"address": "not_an_ip", "port": 8333},
        )
    else:
        assert answer == {"success": False}


def getaddrmaninfo_counts_the_addresses_of_each_network(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Core's `test_getaddrmaninfo`.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    capabilities = (
        Capability.CJDNS,
        Capability.KNOWN_ADDRESSES,
        Capability.ADDRESS_TABLES,
    )
    with _node(
        make_adapter,
        cls,
        executable,
        tmp_path,
        skip_counts,
        _CJDNS_ADDRMAN,
        capabilities,
    ) as node:
        _seed_addrman(node)

        expected_network_count = {
            "all_networks": {"new": 4, "tried": 4, "total": 8},
            "ipv4": {"new": 1, "tried": 1, "total": 2},
            "ipv6": {"new": 1, "tried": 1, "total": 2},
            "onion": {"new": 0, "tried": 2, "total": 2},
            "i2p": {"new": 1, "tried": 0, "total": 1},
            "cjdns": {"new": 1, "tried": 0, "total": 1},
        }
        result = node.rpc.call("getaddrmaninfo")
        for network, count in expected_network_count.items():
            assert result[network]["new"] == count["new"]
            assert result[network]["tried"] == count["tried"]
            assert result[network]["total"] == count["total"]


def _entry(
    bucket_position: str, address: str, network: str, port: int = 8333
) -> dict[str, object]:
    """Return what `getrawaddrman` lists for an address `_seed_addrman` adds."""
    return {
        "bucket_position": bucket_position,
        "address": address,
        "port": port,
        "services": _P2P_SERVICES,
        "network": network,
        "source": address,
        "source_network": network,
    }


def getrawaddrman_lists_the_address_tables(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Core's `test_getrawaddrman`.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    capabilities = (
        Capability.CJDNS,
        Capability.KNOWN_ADDRESSES,
        Capability.ADDRESS_TABLES,
        Capability.CLOCK,
    )
    with _node(
        make_adapter,
        cls,
        executable,
        tmp_path,
        skip_counts,
        _CJDNS_ADDRMAN,
        capabilities,
    ) as node:
        addr_time = int(time.time()) - _A_DAY
        node.set_mock_time(addr_time)
        _seed_addrman(node)
        _is_hidden(node, "getrawaddrman")

        i2p = "c4gfnttsuwqomiygupdqqqyy5y5emnk5c73hrfvatri67prd7vyq.b32.i2p"
        onion_1 = "pg6mmjiyjmcrsslvykfwnntlaru7p5svn6y2ymmju6nubxndf4pscryd.onion"
        onion_2 = "nrfj6inpyf73gpkyool35hcmne5zwfmse3jl3aw23vk7chdemalyaqad.onion"
        ipv6_tried = "1233:3432:2434:2343:3234:2345:6546:4534"
        expected = {
            "new": [
                _entry("82/8", "2.0.0.0", "ipv4"),
                _entry("336/24", "fc00:1:2:3:4:5:6:7", "cjdns"),
                _entry("963/46", i2p, "i2p"),
                _entry("613/6", "2803:0:1234:abcd::1", "ipv6", 45324),
            ],
            "tried": [
                _entry("6/33", "1.2.3.4", "ipv4"),
                _entry("197/34", ipv6_tried, "ipv6"),
                _entry("72/61", onion_1, "onion"),
                _entry("139/46", onion_2, "onion", 45324),
            ],
        }

        raw = node.rpc.call("getrawaddrman")
        info = node.rpc.call("getaddrmaninfo")
        for table, entries in expected.items():
            assert len(raw[table]) == len(entries)
            assert len(raw[table]) == info["all_networks"][table]
            for bucket_position, entry in raw[table].items():
                (want,) = (e for e in entries if e["address"] == entry["address"])
                assert bucket_position == want["bucket_position"]
                for key in ("address", "port", "services", "network"):
                    assert entry[key] == want[key]
                assert entry["source"] == want["source"]
                assert entry["source_network"] == want["source_network"]
                assert entry["time"] == addr_time
