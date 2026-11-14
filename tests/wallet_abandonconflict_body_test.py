# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`wallet_abandonconflict_test`'s body asks for what it uses, first.

The body unloads and loads a wallet, which `WalletCapability.RELOAD`
covers. A factory that does not declare it is refused before any node
restarts, as `CONTRIBUTING.md`'s *Wallets* asks of every wallet body.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn, cast

import pytest

from btclib_functional_tests.capability import (
    Capability,
    MissingCapabilityError,
    SkipCounts,
)
from btclib_functional_tests.wallet import WalletCapability
from tests.integration.wallet_abandonconflict_test import (
    abandon_frees_the_inputs_and_a_conflict_marks_both_sides,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from btclib_functional_tests.node import NodeAdapter
    from btclib_functional_tests.wallet import WalletFactory


class _RestartedError(Exception):
    """A node restarted: the body got past every requirement."""


class _Node:
    """A node declaring every capability, which stops the body at a restart."""

    capabilities = frozenset(Capability)

    def restart(self, args: Sequence[str]) -> NoReturn:
        """Stop the body where it first restarts a node."""
        raise _RestartedError(list(args))


class _Factory:
    """A factory declaring `capabilities`, and asking nothing of a node."""

    node_capabilities: frozenset[Capability] = frozenset()
    node_args: tuple[str, ...] = ()

    def __init__(self, capabilities: frozenset[WalletCapability]) -> None:
        """Declare `capabilities`."""
        self.capabilities = capabilities


def _run(capabilities: frozenset[WalletCapability]) -> None:
    """Run the body over two such nodes and a factory declaring these."""
    nodes = [cast("NodeAdapter", _Node()) for _ in range(2)]
    abandon_frees_the_inputs_and_a_conflict_marks_both_sides(
        lambda count: nodes[:count],
        cast("WalletFactory", _Factory(capabilities)),
        SkipCounts(),
    )


def test_a_factory_without_reload_is_refused_before_any_restart() -> None:
    """The refusal names `wallet_reload`, and comes before a restart."""
    without = frozenset(WalletCapability) - {WalletCapability.RELOAD}
    with pytest.raises(MissingCapabilityError, match="wallet_reload"):
        _run(without)


def test_a_factory_with_every_capability_reaches_the_first_restart() -> None:
    """The control: the same nodes, given `RELOAD`, restart a node."""
    with pytest.raises(_RestartedError):
        _run(frozenset(WalletCapability))
