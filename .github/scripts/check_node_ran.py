# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

r"""Fail a run of `tests/integration` that never reached its node.

pytest exits 0 for a module that skipped itself, so a job whose fixture
stopped finding the node stays green while asking it nothing -- for
`node-integration.yml`'s two btclib-node jobs, an install that succeeds
followed by an `import btclib_node` that raises under
`btclib_node_python`'s probe; for its `btcd` job, a `btcd_path` that
finds no btcd. `reusable-integration-bitcoind.yml`'s "Fail if the node
tests did not run" step is the same question for the `bitcoind` job,
asked there by failing on every skip but one whose reason starts with
the prefix that job declares, `tests/integration/conftest.py`'s
`NO_TARGET`. That cannot hold here: a target node's run skips a test
whose node, or whose wallet, does not declare the capability it needs,
and rule 4 of issue btclib-org/btclib#2220 makes that skip a counted
result rather than a defect.

So this fails on a report holding no testcase, or on a skip whose
message is not a capability's. A capability's message is read from
`capability.require` itself, by making it refuse each `Capability` and
each `wallet.WalletCapability` in turn, rather than copied here: the
wording then has one home, and a change to it moves this check with it.
Every other skip -- no `btclib_node` importable, no `bitcoind`,
`TF2_INTEGRATION` unset -- is a test that never reached a node, and
fails the run. A capability's skip does not: the fixture that found the
node importable has already run by the time a test asks for one.

    python .github/scripts/check_node_ran.py integration.xml
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from btclib_functional_tests.capability import (
    Capability,
    MissingCapabilityError,
    SkipCounts,
    require,
)
from btclib_functional_tests.wallet import WalletCapability


def capability_messages() -> frozenset[str]:
    """Return every message `capability.require`'s refusal can carry."""
    messages: set[str] = set()
    # one loop per enum: `require` takes one of the two, never their union,
    # and a loop over both would type `capability` as `Any`
    for capability in Capability:
        try:
            require(capability, frozenset(), SkipCounts())
        except MissingCapabilityError as exc:
            messages.add(str(exc))
    for wallet_capability in WalletCapability:
        try:
            require(wallet_capability, frozenset(), SkipCounts())
        except MissingCapabilityError as exc:
            messages.add(str(exc))
    return frozenset(messages)


def unreached(report: Path) -> tuple[int, list[tuple[str, str]]]:
    """Return how many testcases a report holds, and its non-capability skips.

    :param report: a JUnit report, as `pytest --junitxml` writes it.
    :returns: how many testcases the report holds, and the name and skip
        message of each one that skipped for a reason other than a
        capability.
    """
    declared = capability_messages()
    cases = list(ET.parse(report).getroot().iter("testcase"))  # noqa: S314
    skips: list[tuple[str, str]] = []
    for case in cases:
        skipped = case.find("skipped")
        if skipped is None:
            continue
        message = skipped.get("message", "")
        if message not in declared:
            skips.append((case.get("name", ""), message))
    return len(cases), skips


def main() -> int:
    """Check the report named on the command line; 1 where no node was reached.

    A usage error is the only way this exits 2, the workflow step passing
    the report every time.
    """
    args = sys.argv[1:]
    if len(args) != 1:
        print(f"usage: {Path(sys.argv[0]).name} <junit.xml>", file=sys.stderr)
        return 2
    total, skips = unreached(Path(args[0]))
    for name, message in skips:
        print(f"::error::{name} skipped: {message}")
    if not total or skips:
        print(
            f"::error::{total} test(s) collected, {len(skips)} skipped for a"
            " reason other than a capability the node or the wallet does not"
            " declare"
        )
        return 1
    print(
        f"{total} test(s) collected, none skipped but for a capability the"
        " node or the wallet does not declare"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
