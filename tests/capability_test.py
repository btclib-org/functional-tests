# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""`Capability.require` skips exactly on a missing capability, and counts it."""

from __future__ import annotations

import pytest

from btclib_functional_tests.capability import (
    Capability,
    MissingCapabilityError,
    SkipCounts,
    require,
)
from btclib_functional_tests.wallet import WalletCapability
from tests.conftest import pytest_runtest_call


def test_require_passes_through_a_declared_capability() -> None:
    """A capability the node declares is answered by returning, not skipping."""
    counts = SkipCounts()
    require(Capability.MINE, frozenset({Capability.MINE}), counts)
    assert not list(counts)


def test_require_skips_a_missing_capability() -> None:
    """A capability the node does not declare is a `MissingCapabilityError`."""
    counts = SkipCounts()
    with pytest.raises(MissingCapabilityError, match="connect"):
        require(Capability.CONNECT, frozenset(), counts)
    assert list(counts) == [("connect", 1)]


def test_require_counts_every_skip_against_its_own_capability() -> None:
    """Two skips of the same capability are one count of two, not two rows."""
    counts = SkipCounts()
    for _ in range(2):
        with pytest.raises(MissingCapabilityError):
            require(Capability.MINE, frozenset(), counts)
    with pytest.raises(MissingCapabilityError):
        require(Capability.CONNECT, frozenset(), counts)
    assert list(counts) == [("connect", 1), ("mine", 2)]


def test_report_names_no_test_where_nothing_was_recorded() -> None:
    """An empty session's own report says so instead of printing nothing."""
    assert (
        SkipCounts().report() == "skips per capability: no test asked for one this run"
    )


def test_report_names_every_recorded_capability() -> None:
    """The report holds one line per capability, sorted by its own value."""
    counts = SkipCounts()
    with pytest.raises(MissingCapabilityError):
        require(Capability.MINE, frozenset(), counts)
    with pytest.raises(MissingCapabilityError):
        require(Capability.CONNECT, frozenset(), counts)
    report = counts.report()
    assert report == "skips per capability:\nconnect: 1\nmine: 1"


def test_as_mapping_is_a_plain_dict_keyed_by_capability_value() -> None:
    """`as_mapping` is what crosses an xdist worker boundary, so no `Enum`."""
    counts = SkipCounts()
    with pytest.raises(MissingCapabilityError):
        require(Capability.MINE, frozenset(), counts)
    with pytest.raises(MissingCapabilityError):
        require(Capability.MINE, frozenset(), counts)
    with pytest.raises(MissingCapabilityError):
        require(Capability.CONNECT, frozenset(), counts)
    assert counts.as_mapping() == {"mine": 2, "connect": 1}


def test_add_mapping_is_as_mapping_s_own_inverse() -> None:
    """A tally built from another's `as_mapping` reports the same counts."""
    original = SkipCounts()
    with pytest.raises(MissingCapabilityError):
        require(Capability.MINE, frozenset(), original)
    with pytest.raises(MissingCapabilityError):
        require(Capability.RAW_MESSAGE, frozenset(), original)

    rebuilt = SkipCounts()
    rebuilt.add_mapping(original.as_mapping())
    assert list(rebuilt) == list(original)


def test_add_mapping_adds_onto_what_is_already_there() -> None:
    """Folding a second worker's tally in credits both, not the last one."""
    counts = SkipCounts()
    with pytest.raises(MissingCapabilityError):
        require(Capability.MINE, frozenset(), counts)
    counts.add_mapping({"mine": 2, "connect": 1})
    assert list(counts) == [("connect", 1), ("mine", 3)]


def test_add_mapping_refuses_a_value_of_no_capability() -> None:
    """A key no enum holds is refused, and nothing is added."""
    counts = SkipCounts()
    with pytest.raises(ValueError, match="not a capability: 'no_such'"):
        counts.add_mapping({"mine": 1, "no_such": 1})
    assert not list(counts)


def test_add_mapping_takes_a_wallet_capability_s_value() -> None:
    """A wallet capability's value is a capability, as a node's is."""
    counts = SkipCounts()
    counts.add_mapping({WalletCapability.RELOAD.value: 2})
    assert list(counts) == [(WalletCapability.RELOAD.value, 2)]


def test_a_wallet_capability_is_refused_as_the_wallet_s() -> None:
    """A missing wallet capability names the wallet, and is counted too."""
    counts = SkipCounts()
    with pytest.raises(
        MissingCapabilityError, match="^wallet does not declare wallet_reload$"
    ):
        require(WalletCapability.RELOAD, frozenset(), counts)
    require(WalletCapability.RELOAD, frozenset({WalletCapability.RELOAD}), counts)
    assert list(counts) == [("wallet_reload", 1)]


def test_no_wallet_capability_shares_a_node_capability_s_value() -> None:
    """The tally is keyed on the value, so two members never share one."""
    node_values = {capability.value for capability in Capability}
    wallet_values = {capability.value for capability in WalletCapability}
    assert wallet_values
    assert node_values.isdisjoint(wallet_values)


def test_the_hookwrapper_generator_converts_a_thrown_exception() -> None:
    """`pytest_runtest_call`'s own generator, driven the way pluggy drives it.

    A `wrapper=True` hookimpl's generator is exactly what pluggy's own
    `_multicall` sends a result into or throws an exception into at its
    `yield` -- unlike a plain autouse fixture's teardown, which only ever
    calls `next` regardless of the wrapped test's own outcome (the shape
    this hook replaced, and the reason a manual `throw` on that one
    would have proven nothing about a real pytest run).
    """
    call = pytest_runtest_call(item=None)  # type: ignore[arg-type]
    next(call)
    with pytest.raises(pytest.skip.Exception, match="mine"):
        call.throw(MissingCapabilityError("node does not declare mine"))


def test_the_hookwrapper_turns_the_exception_into_a_skip(
    pytester: pytest.Pytester,
) -> None:
    """`tests/conftest.py`'s own translation, driven through a real session.

    The unit test above drives the generator directly, matching pluggy's
    own protocol; this one confirms the same thing end to end, through
    an actual nested pytest session.
    """
    pytester.makeconftest(
        """
        import pytest
        from btclib_functional_tests.capability import MissingCapabilityError

        @pytest.hookimpl(wrapper=True)
        def pytest_runtest_call(item):
            del item
            try:
                return (yield)
            except MissingCapabilityError as exc:
                pytest.skip(str(exc))
        """
    )
    pytester.makepyfile(
        """
        from btclib_functional_tests.capability import MissingCapabilityError

        def test_it():
            raise MissingCapabilityError("node does not declare mine")
        """
    )
    result = pytester.runpytest()
    result.assert_outcomes(skipped=1)
