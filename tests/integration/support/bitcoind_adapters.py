# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`BitcoindAdapter` subclasses for `*_bitcoind_test.py` modules.

A module hands one to the test body it runs.

This module holds no test: its name ends `_test` for the repository's
`name-tests-test` hook.
"""

from __future__ import annotations

from typing import override

from btclib_functional_tests.bitcoind import BitcoindAdapter

__all__ = ["UnboundBitcoindAdapter"]


class UnboundBitcoindAdapter(BitcoindAdapter):
    """`BitcoindAdapter` without the `-bind` its `_command` names.

    A test that names where the node listens needs the node without the
    adapter's own `-bind`. An `extra_args` entry cannot replace it,
    `NodeAdapter` refusing one naming an option `_command()` already sets,
    so `_command` drops it.
    """

    @override
    def _command(self) -> list[str]:
        """Return the base command without its `-bind`."""
        return [arg for arg in super()._command() if not arg.startswith("-bind=")]
