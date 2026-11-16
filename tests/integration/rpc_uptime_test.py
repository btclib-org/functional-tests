# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Core's `rpc_uptime`, one body over each node.

Read from Core's `test/functional/rpc_uptime.py` (`406c2348ddbf`,
2026-06-13) rather than ported: that file drives a `TestNode` over
Core's own harness and reads `time.sleep`/`uptime`/`setmocktime` as
`TestNode` attributes. This rewrites the same subject over `NodeAdapter`
(rule 5 of issue btclib-org/btclib#2220: tf2's own adapter). No
deviation from Core's own claim on a build with bitcoin/bitcoin#34328: a
single clean-chain node, `uptime` before and after `Capability.CLOCK`
(`capability.py`) jumps the clock forward, is the whole of what either
file asks.

Each `rpc_uptime_<node>_test.py` module beside this one runs it,
`tests/integration/conftest.py`'s own module docstring having how.

A bitcoind before `v31.0` counts `uptime` from the wall clock, so it
follows `setmocktime` (bitcoin/bitcoin#34328). No probe tells the two
apart, the node having no option or RPC for it: that build is asserted to
follow the mock clock, as Core's own file did there, read off its own
`getnetworkinfo` `version`
([ISS 354](https://github.com/btclib-org/functional-tests/issues/354)).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from btclib_functional_tests.capability import Capability, require
from tests.integration.support.script_verify_flag import bitcoind_version

if TYPE_CHECKING:
    from btclib_functional_tests.capability import SkipCounts
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["uptime_does_not_jump_with_the_wall_clock"]

_WAIT_TIME = 20_000

# Core's own `CLIENT_VERSION`, at or past which `uptime` is monotonic:
# `v31.0`'s (bitcoin/bitcoin#34328). A known limit: a `master` build from
# that change's merge (`27aeeff630`, 2026-01-27) until the version moved to
# `31.99` (`48b952cbb6`, 2026-03-06) reports `309900` and is monotonic all
# the same, so this test fails against such a build
_MONOTONIC_UPTIME_VERSION = 310000


def uptime_does_not_jump_with_the_wall_clock(
    adapter: NodeAdapter, skip_counts: SkipCounts
) -> None:
    """Core's own subject: `uptime` does not follow the mock clock.

    A bitcoind before `_MONOTONIC_UPTIME_VERSION` follows it instead.

    :param adapter: a node's own adapter fixture.
    :param skip_counts: the session's own tally.
    """
    require(Capability.CLOCK, adapter.capabilities, skip_counts)
    time.sleep(1)  # let some real time pass before the first reading
    uptime_before = adapter.rpc.call("uptime")
    assert isinstance(uptime_before, int)
    assert uptime_before > 0

    adapter.set_mock_time(int(time.time()) + _WAIT_TIME)
    try:
        uptime_after = adapter.rpc.call("uptime")
    finally:
        adapter.set_mock_time(0)  # released even where the call above raises
    assert isinstance(uptime_after, int)
    version = bitcoind_version(adapter)
    if version is not None and version < _MONOTONIC_UPTIME_VERSION:
        assert uptime_after >= _WAIT_TIME
    else:
        assert uptime_after - uptime_before < _WAIT_TIME
