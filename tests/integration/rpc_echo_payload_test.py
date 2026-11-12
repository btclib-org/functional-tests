# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_echo_payload`, one body over each node.

Read from Core's `test/functional/rpc_echo_payload.py` (`fa7bc26d1276`,
2026-08-06), an option-family row
([ISS functional-tests#3](https://github.com/btclib-org/functional-tests/issues/3)):
a node restarted with `-rpcworkqueue=2` and `-rpcthreads=2`
(`Capability.RPC_WORK_QUEUE`) takes payloads of random length, large
ones included, from more concurrent callers than its threads and its
queue hold, each payload sent through `echo` or `sendrawtransaction`
chosen at random, and answers or refuses every request rather than
leaving one to time out: a timeout is `bitcoin_core_rpc`'s own
`FetchError`, which neither `except` clause of `_call_repeatedly`
catches.

A smaller claim than Core's own test, declared rather than silent: Core
matches a refusal on its message, "Work queue depth exceeded" after the
HTTP 503 status, where `bitcoin_core_rpc`'s own `HttpError` carries the
status alone, the body of a non-JSON reply not being kept, so a refusal
here is the 503 status. Each caller keeps one connection of its own
across its calls, the adapter holding one per thread (`node.py`'s
`_ThreadSessions`) the way Core's own threads each hold the one
`create_new_rpc_connection` gives them, so up to `_CALLERS` requests
are in flight at once.

A bitcoind serving HTTP through libevent, as every release before `v32.0`
does (bitcoin/bitcoin#35182, first in `v32.0rc1`), can leave a request on a
kept-alive connection unanswered. The pull
request adding Core's file (bitcoin/bitcoin#34927) failed in CI on it until
libevent was removed, and notes that a new connection works around it.
So a build whose `logging` lists the `libevent` category, which
bitcoin/bitcoin#35597 removed, is called over a connection of its own per
call, as `urlopen_transport` makes one. That is the one difference from
Core's file, which keeps each caller's connection.

Each `rpc_echo_payload_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.
"""

from __future__ import annotations

import random
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from bitcoin_core_rpc import (
    BitcoinCoreRpcClient,
    HttpError,
    RpcError,
    urlopen_transport,
)

from btclib_functional_tests.bitcoind import BitcoindAdapter
from btclib_functional_tests.capability import Capability, require

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = [
    "a_payload_is_answered_or_refused_never_left_to_time_out",
    "client_per_call",
    "serves_through_libevent",
]

# Core's own `set_test_params`: enough callers to fill both worker threads
# and the queue behind them
_CALLERS = 6
_CALLS_PER_CALLER = 200

# `sendrawtransaction`'s own answer to a payload that is no transaction
_DECODE_FAILED = "TX decode failed. Make sure the tx has at least one input."

# the status `src/httpserver.cpp` refuses a request with once the queue is full
_SERVICE_UNAVAILABLE = 503


def serves_through_libevent(node: NodeAdapter) -> bool:
    """Whether `node` is a bitcoind listing the `libevent` log category.

    `logging` lists every category; a build past bitcoin/bitcoin#35597,
    first in `v32.0rc1`, lists none of that name.
    Any node but a bitcoind answers no.
    """
    if not isinstance(node, BitcoindAdapter):
        return False
    categories = node.rpc.call("logging")
    assert isinstance(categories, dict)
    return "libevent" in categories


def client_per_call(node: NodeAdapter) -> BitcoinCoreRpcClient:
    """Return `node`'s client over a connection of its own for each call.

    Covers a node authenticating by its cookie, as the cluster's are, and
    not `--tracerpc`'s printing, which `NodeAdapter._rpc_transport` adds.
    """
    client = node.rpc
    assert client.user is None, "a node with credentials of its own"
    return BitcoinCoreRpcClient(
        client.url,
        cookie_path=client.cookie_path,
        timeout=client.timeout,
        transport=urlopen_transport,
    )


def _call_repeatedly(rpc: BitcoinCoreRpcClient, data: str) -> None:
    """Send random prefixes of `data`, accepting an answer or a refusal only."""
    for _ in range(_CALLS_PER_CALLER):
        payload = data[: random.randrange(len(data))]
        try:
            if random.getrandbits(1):
                assert rpc.call("echo", [payload]) == [payload]
            else:
                rpc.call("sendrawtransaction", [payload])
        except RpcError as e:
            if _DECODE_FAILED not in str(e):
                raise
        except HttpError as e:
            if e.status != _SERVICE_UNAVAILABLE:
                raise


def a_payload_is_answered_or_refused_never_left_to_time_out(
    cluster: Callable[[int], Sequence[NodeAdapter]],
    skip_counts: SkipCounts,
) -> None:
    """Core's own subject: every request is answered or refused in time.

    :param cluster: a node's own cluster fixture.
    :param skip_counts: the session's own tally.
    """
    (node,) = cluster(1)
    require(Capability.RPC_WORK_QUEUE, node.capabilities, skip_counts)
    node.restart(["-rpcworkqueue=2", "-rpcthreads=2"])
    # json-serializable, but not hex
    data = f"z{random.randbytes(1_999_000).hex()}"
    per_call = serves_through_libevent(node)
    with ThreadPoolExecutor(max_workers=_CALLERS) as callers:
        futures = [
            callers.submit(
                _call_repeatedly, client_per_call(node) if per_call else node.rpc, data
            )
            for _ in range(_CALLERS)
        ]
    for future in futures:
        future.result()
