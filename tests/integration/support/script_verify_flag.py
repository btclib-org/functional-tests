# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""What a node's own `getnetworkinfo` `version` says about its wording.

A block, or a transaction in one, that fails script verification is
refused with `block-script-verify-flag-failed` from `v30.0` and with
`mandatory-script-verify-flag-failed` before it (bitcoin/bitcoin#33183).

This module holds no test: its name ends `_test` for the repository's
`name-tests-test` hook.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from btclib_functional_tests.bitcoind import BitcoindAdapter

if TYPE_CHECKING:
    from btclib_functional_tests.node import NodeAdapter

__all__ = ["bitcoind_version", "block_script_verify_flag_failed"]

# the `CLIENT_VERSION` (`src/clientversion.h`) of `v30.0`, the first release
# with the `block-` wording (bitcoin/bitcoin#33183)
_BLOCK_WORDING_VERSION = 300000


def bitcoind_version(node: NodeAdapter) -> int | None:
    """Return `node`'s own `getnetworkinfo` `version`, `None` off bitcoind.

    ([ISS 35](https://github.com/btclib-org/functional-tests/issues/35))
    """
    if not isinstance(node, BitcoindAdapter):
        return None
    info = node.rpc.call("getnetworkinfo")
    assert isinstance(info, dict)
    version = info["version"]
    assert isinstance(version, int)
    return version


def block_script_verify_flag_failed(node: NodeAdapter) -> str:
    """Return the reason `node` gives a block failing script verification.

    `mandatory-` from a bitcoind before `v30.0`, `block-` from any other node.
    """
    version = bitcoind_version(node)
    if version is not None and version < _BLOCK_WORDING_VERSION:
        return "mandatory-script-verify-flag-failed"
    return "block-script-verify-flag-failed"
