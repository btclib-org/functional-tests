# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `feature_anchors`, as bodies over each node.

Read from Core's `test/functional/feature_anchors.py` (`2f7622e3f6f7`,
2026-10-17): a node stopped cleanly writes the addresses of its
block-relay-only outbound peers, and no other, to `anchors.dat` in its
chain directory, reads the file back on the next start, deletes it, and
dials the addresses it held.

Each of Core's checks is a body over a fresh node:

- block-relay-only peers the node dials (`Capability.TYPED_OUTBOUND`)
  are in the file its stop writes, and inbound peers are not; a file
  one octet longer than the node wrote is read as no anchor at all,
  logged as Core logs it, and deleted all the same.
- an address of a network `addr` cannot carry, a Tor v3 one the node
  dials through a `Socks5Proxy` given as `-onion`
  (`Capability.PROXY`), is written in BIP155's encoding with no service
  flag, and is dialled on the next start once the file names a peer
  offering the services the node asks for.

Core's steps for bitcoin/bitcoin#34213 are in those two bodies: the
anchors survive a network switched off. A node stopped after
`setnetworkactive` false still writes the block-relay-only peers it
held; one started with `-networkactive=0` neither tries nor deletes the
file; the anchors are tried once the network is switched on; and anchors
never tried are discarded at the next shutdown with the network active.
They ask for `Capability.SUSPEND_NETWORK` besides. No tag up to `v32.1`
carries the change, so they run on a build whose `getnetworkinfo`
`version` is `329900` or later. A `master` build from `32.99`'s move
(`f3fec67c3e`, 2026-09-11) to the merge of that change (`802eb7daa9`,
2026-10-02) reports `329900` without it, and fails them. Core's
`-seednode` given twice, an address nothing listens on
(`Capability.ADDRESS_FETCH`), and a clock moved past the seednode retry
timer (`Capability.CLOCK`), both asked for there, show the node has
passed its connection loop with the network off.

Every log line is one Core's own file asserts, over `Capability.DEBUG_LOG`.

Core runs both checks over one node, the second after the file the first
leaves is deleted; here the second starts with `-onion` from its first
start rather than restarting into it. Each line is awaited over
`assert_debug_log`'s own default wait, where Core reads its log once the
block exits, or for two seconds for the last line.
`anchors.dat` is read at `datadir / "regtest"`, Core's `chain_path`, as
`feature_blocksxor_test.py` reads the block files beside it.

Each `feature_anchors_<node>_test.py` module beside this one runs each
body, `tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import time
from base64 import b32decode
from contextlib import ExitStack
from typing import TYPE_CHECKING

from bitcoin_core_rpc import magic_from_chain
from btclib.hashes import hash256
from btclib.p2p import ServiceFlags

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require
from btclib_functional_tests.debug_log import assert_debug_log
from btclib_functional_tests.node import free_ports, wait_until
from btclib_functional_tests.peer import Listener, Peer
from btclib_functional_tests.socks5 import Socks5Proxy
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from pathlib import Path

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter
    from tests.conftest import AdapterFactory

__all__ = [
    "block_relay_only_peers_are_the_anchors",
    "onion_anchor_is_dumped_and_dialled",
]

_MAGIC = magic_from_chain("regtest")

# Core's own counts of each kind of peer
_INBOUND_CONNECTIONS = 5
_BLOCK_RELAY_CONNECTIONS = 2

# Core's own `ONION_ADDR`, host and port
_ONION_HOST = "pg6mmjiyjmcrsslvykfwnntlaru7p5svn6y2ymmju6nubxndf4pscryd.onion"
_ONION_PORT = 8333
_ONION_ADDR = f"{_ONION_HOST}:{_ONION_PORT}"

# `127.0.0.1` as Core's own file spells it in hex
_LOOPBACK_HEX = "7f000001"

# the file's first address's services: after the network magic, the count,
# `CAddress`'s disk version and the address's own timestamp
_SERVICES_INDEX = 4 + 1 + 4 + 4

# the double SHA-256 closing the file
_CHECKSUM_SIZE = 32

# Core's own line for a stop writing one anchor
_DUMPED_ONE = (
    "DumpAnchors: Flush 1 outbound block-relay-only peer addresses to anchors.dat"
)

# Core's own `P2P_SERVICES` (`test_framework/p2p.py`)
_P2P_SERVICES = ServiceFlags.NODE_NETWORK | ServiceFlags.NODE_WITNESS

# the `getnetworkinfo` `version` from which anchors survive a network
# switched off; the module docstring has why
_ANCHORS_SURVIVE_VERSION = 329900

# Core's own `SEED_NODE`, an address nothing listens on, given twice as
# `-seednode`, and the line the node logs for each
_SEED_NODE = "127.0.0.1:1"
_SEED_ARGS = [f"-seednode={_SEED_NODE}", f"-seednode={_SEED_NODE}"]
_SEED_LOG = f"adding seednode ({_SEED_NODE}) to addrfetch"

# the retry timer of the second seednode, Core's `ADD_NEXT_SEEDNODE`, in
# seconds
_ADD_NEXT_SEEDNODE = 10
_ANCHORS_TRIED = "block-relay-only anchors will be tried for connections"


def _node(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    datadir: Path,
    extra_args: list[str],
) -> NodeAdapter:
    """Build a node over `datadir`, started with `extra_args` by default."""
    rpc_port, p2p_port = free_ports(2)
    return make_adapter(cls, executable, datadir, rpc_port, p2p_port, extra_args)


def _debug_log(node: NodeAdapter, skip_counts: SkipCounts) -> Path:
    """Return the log Core's own lines are read from, or skip.

    :raises TypeError: `node` declares `Capability.DEBUG_LOG` without
        being the adapter that names a `debug_log_path`.
    """
    require(Capability.DEBUG_LOG, node.capabilities, skip_counts)
    if not isinstance(node, BitcoindAdapter):
        err_msg = f"{type(node).__name__} declares DEBUG_LOG, naming no debug.log"
        raise TypeError(err_msg)
    return node.debug_log_path


def _connected(peer: Peer) -> Peer:
    """Complete `peer`'s handshake, then a ping round trip, and return it."""
    try:
        peer.handshake()
        peer.sync_with_ping()
    except BaseException:
        peer.close()
        raise
    return peer


def _anchors_survive_a_network_toggle(
    node: NodeAdapter,
) -> bool:
    """Return whether this build keeps anchors across a switched-off network.

    Read per build ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35)):
    a node other than bitcoind is held to it.
    """
    version = bitcoind_version(node)
    return version is None or version >= _ANCHORS_SURVIVE_VERSION


def _wait_for_an_open_connections_pass(node: NodeAdapter, log_path: Path) -> None:
    """Core's own `wait_for_open_connections_pass`, over `-seednode` twice.

    The retry timer is checked right before the anchors are selected and
    reads the node's clock, so one past `_ADD_NEXT_SEEDNODE` makes the
    next pass queue the second seednode, logged at the top of the one
    after.
    """
    with assert_debug_log(log_path, [_SEED_LOG]):
        node.set_mock_time(int(time.time()) + _ADD_NEXT_SEEDNODE + 1)


def _hex_port(address: str) -> str:
    """Core's own: the port of `getpeerinfo`'s `addr`, as four hex digits.

    The file stores a port as two bytes, so a port below `0x1000` is
    zero-padded.
    """
    return f"{int(address.split(':')[1]):04x}"


def block_relay_only_peers_are_the_anchors(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check `anchors.dat` holds the block-relay-only peers alone.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    datadir = tmp_path / "datadir"
    node = _node(make_adapter, cls, executable, datadir, [])
    require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
    log_path = _debug_log(node, skip_counts)
    anchors_path = datadir / "regtest" / "anchors.dat"
    try:
        node.start()
        assert not anchors_path.exists()

        with ExitStack() as peers:
            for _ in range(_BLOCK_RELAY_CONNECTIONS):
                with Listener(_MAGIC) as listener:
                    node.add_outbound_connection(listener.address, "block-relay-only")
                    peer = listener.accept()
                peers.enter_context(_connected(peer))
            for _ in range(_INBOUND_CONNECTIONS):
                peers.enter_context(_connected(Peer(node.p2p_address, _MAGIC)))

            info = node.rpc.call("getnetworkinfo")
            assert info["connections_in"] == _INBOUND_CONNECTIONS
            assert info["connections_out"] == _BLOCK_RELAY_CONNECTIONS

            block_relay_ports = []
            inbound_ports = []
            for peer_info in node.rpc.call("getpeerinfo"):
                port = _hex_port(peer_info["addr"])
                if peer_info["connection_type"] == "block-relay-only":
                    block_relay_ports.append(port)
                else:
                    inbound_ports.append(port)

            if _anchors_survive_a_network_toggle(node):
                require(Capability.SUSPEND_NETWORK, node.capabilities, skip_counts)
                node.rpc.call("setnetworkactive", [False])
                wait_until(lambda: node.rpc.call("getpeerinfo") == [])

            node.stop()

        anchors = anchors_path.read_bytes()
        anchors_hex = anchors.hex()
        for port in block_relay_ports:
            assert _LOOPBACK_HEX + port in anchors_hex
        for port in inbound_ports:
            assert _LOOPBACK_HEX + port not in anchors_hex

        with assert_debug_log(
            log_path, ["0 block-relay-only anchors will be tried for connections."]
        ):
            tweaked = bytearray(anchors)
            tweaked[20:20] = b"1"
            anchors_path.write_bytes(bytes(tweaked))
            node.start()

        assert not anchors_path.exists()
    finally:
        node.stop()


def onion_anchor_is_dumped_and_dialled(
    make_adapter: AdapterFactory,
    cls: type[NodeAdapter],
    executable: str,
    tmp_path: Path,
    skip_counts: SkipCounts,
) -> None:
    """Check a Tor v3 anchor is written in BIP155's encoding and dialled.

    :param make_adapter: the session's own adapter factory.
    :param cls: a node's own adapter class.
    :param executable: the node's binary, or the interpreter running it.
    :param tmp_path: where the node's own data directory goes.
    :param skip_counts: the session's own tally.
    """
    datadir = tmp_path / "datadir"
    with Socks5Proxy(authentication=True) as proxy:
        onion_arg = f"-onion={proxy.endpoint}"
        node = _node(make_adapter, cls, executable, datadir, [onion_arg])
        require(Capability.TYPED_OUTBOUND, node.capabilities, skip_counts)
        require(Capability.PROXY, node.capabilities, skip_counts)
        log_path = _debug_log(node, skip_counts)
        anchors_path = datadir / "regtest" / "anchors.dat"
        try:
            node.start()
            survives = _anchors_survive_a_network_toggle(node)
            if survives:
                require(Capability.ADDRESS_FETCH, node.capabilities, skip_counts)
                require(Capability.CLOCK, node.capabilities, skip_counts)
                require(Capability.SUSPEND_NETWORK, node.capabilities, skip_counts)
            node.add_outbound_connection((_ONION_HOST, _ONION_PORT), "block-relay-only")
            with assert_debug_log(log_path, [_DUMPED_ONE]):
                node.stop()
            proxy.close()

            # the address's ed25519 public key, Core's own
            # `CAddress.serialize_v2()[7:39]` for a Tor v3 address
            expected_pubkey = b32decode(_ONION_HOST.removesuffix(".onion"), True)[:32]
            data = anchors_path.read_bytes()
            assert data[_SERVICES_INDEX] == ServiceFlags.NODE_NONE
            assert expected_pubkey.hex() in data.hex()

            # the node dials no anchor lacking the services it asks for,
            # so the file is rewritten naming them, its checksum with it
            new_data = bytearray(data)[:-_CHECKSUM_SIZE]
            new_data[_SERVICES_INDEX] = _P2P_SERVICES
            anchors_file = bytes(new_data) + hash256(new_data)
            anchors_path.write_bytes(anchors_file)

            if survives:
                # started with the network off, the node keeps the file
                with assert_debug_log(log_path, [_SEED_LOG], [_ANCHORS_TRIED]):
                    node.restart(["-networkactive=0", *_SEED_ARGS])
                _wait_for_an_open_connections_pass(node, log_path)
                node.stop()

            dialling = f"Trying to make an anchor connection to {_ONION_ADDR}"
            with assert_debug_log(log_path, [dialling]):
                node.start()

            if survives:
                # switched on, the node tries them
                node.stop()
                anchors_path.write_bytes(anchors_file)
                with assert_debug_log(log_path, [_SEED_LOG]):
                    node.restart(["-networkactive=0", onion_arg, *_SEED_ARGS])
                _wait_for_an_open_connections_pass(node, log_path)
                with assert_debug_log(log_path, [dialling], timeout=2):
                    node.rpc.call("setnetworkactive", [True])

                # anchors never tried are dropped at the next stop
                node.stop()
                anchors_path.write_bytes(anchors_file)
                with assert_debug_log(log_path, [f"1 {_ANCHORS_TRIED}"]):
                    node.restart(["-maxconnections=8"])
                with assert_debug_log(
                    log_path,
                    ["DumpAnchors: Flush 0 outbound block-relay-only peer addresses"],
                ):
                    node.stop()
        finally:
            node.stop()
