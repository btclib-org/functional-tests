# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

r"""Compare a btclib-wallet run of `tests/integration` with `TF2.md`'s table.

`node-integration.yml`'s `btclib-wallet` and `btclib-wallet-main` jobs
run every `*_btclib_wallet_test.py` module, and a test can fail there by
design: a **fail** cell of `TF2.md`'s *The btclib-wallet ledger* names
the btclib-wallet issue it waits on. So pytest's own exit status says
nothing about whether anything changed. This script says it instead:
each row of that table against the testcases of the JUnit report that
row covers, printing every row whose outcome moved.

**Which verdict a cell gives for which build.** A cell is a single
verdict, true of every build, or two `; `-separated segments:
`<verdict> on the build`, the PyPI release the `btclib-wallet` job
installs (`release` below), then `<verdict> on a build past [ISS ...]`,
a build carrying that issue's fix, which btclib-wallet's own `main` (the
`btclib-wallet-main` job, `main` below) is read as being.

A verdict is `pass`, `fail ([ISS ...](...))`,
`skip (<capability>, [ISS ...](...))`, `stub ([ISS ...](...))` or
`flaky ([ISS ...](...))`, which the table's own section defines. A cell
or a segment in any other shape raises `LedgerError` rather than being
read as some verdict. A **fail** and a **stub** row both expect the row
to fail, and a **flaky** row expects it to pass or to fail; which
capability a skip names is not compared.

**What a row's outcome is.** A row's testcases are read together: the
row failed where any of them failed, skipped where none failed and any
skipped, and passed where every one passed.

**A flaky row that passed is listed apart.** It agrees with its cell, so
it is no move, and the exit status does not change. The table's own
section says when such a verdict moves to **pass**, which turns on the
issue the cell names, and this reads no issue.

**Which testcase is which row's.** `_ROWS` pairs a module stem, less its
`_btclib_wallet_test` suffix, or `module::function` where one module's
tests are several rows', with the row's own first cell.
`btclib_wallet_verdict_test.py` holds the table to the tree both ways.

It exits 1 wherever anything moved, a row that now passes included, and
writes to stdout alone, which the workflow step copies into
`$GITHUB_STEP_SUMMARY`:

    uv run --no-sync python .github/scripts/btclib_wallet_verdict.py \
        TF2.md integration.xml release
"""

from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

BUILDS = ("release", "main")

_SECTION = "## The btclib-wallet ledger"

_ISS = r"\[ISS [^\]]+\]\([^)\s]+\)"

# a verdict, and the outcome a row's testcases are expected to have
_VERDICTS = (
    (re.compile(r"pass"), "pass"),
    (re.compile(rf"fail \({_ISS}\)"), "fail"),
    (re.compile(rf"stub \({_ISS}\)"), "fail"),
    (re.compile(rf"skip \(\w+, {_ISS}\)"), "skip"),
    (re.compile(rf"flaky \({_ISS}\)"), "flaky"),
)

# the outcomes each expected outcome agrees with
_AGREES = {
    "pass": {"pass"},
    "fail": {"fail"},
    "skip": {"skip"},
    "flaky": {"pass", "fail"},
}

_ON_THE_BUILD = re.compile(r"(?P<verdict>.+) on the build")
_ON_A_BUILD_PAST = re.compile(rf"(?P<verdict>.+) on a build past {_ISS}")

_SUFFIX = "_btclib_wallet_test"

# a module stem, or `stem::function`, against the ledger row its tests
# are -- the row's own first cell, verbatim. A function entry wins over
# its module's
_ROWS: dict[str, str] = {
    "wallet_balance": "`wallet_balance.py`",
    "wallet_abandonconflict": "`wallet_abandonconflict.py`",
    "wallet_txn_doublespend::test_a_double_spend_conflicts_the_wallet_s_payments": (
        "`wallet_txn_doublespend.py`"
    ),
    "wallet_txn_doublespend::test_a_double_spend_conflicts_the_wallet_s_confirmed_payments": (
        "`wallet_txn_doublespend.py` (`--mineblock`)"
    ),
}

_KIND = {
    ("pass", "fail"): "regression",
    ("pass", "skip"): "skipped",
    ("fail", "pass"): "fixed",
    ("fail", "skip"): "skipped",
    ("skip", "pass"): "ran",
    ("skip", "fail"): "ran, and failed",
    ("flaky", "skip"): "skipped",
}


class LedgerError(ValueError):
    """A btclib-wallet ledger row, or a cell of it, this cannot read."""


def _outcome_of(row: str, text: str) -> str:
    """Return the outcome one verdict expects, less any build qualifier.

    :param row: the row's own first cell, named in a refusal.
    :param text: the verdict.
    :raises LedgerError: where the text is no verdict the table defines.
    """
    for pattern, outcome in _VERDICTS:
        if pattern.fullmatch(text):
            return outcome
    msg = f"{row}: no verdict reads {text!r}"
    raise LedgerError(msg)


def expected(row: str, cell: str, build: str) -> tuple[str, str]:
    """Return the verdict a cell gives one build, and the outcome it expects.

    :param row: the row's own first cell, named in a refusal.
    :param cell: the row's `btclib-wallet` cell.
    :param build: `"release"` or `"main"`.
    :raises LedgerError: where a segment is in no shape the table
        defines, or where the cell does not give that build exactly one
        verdict.
    """
    segments = cell.split("; ")
    if len(segments) == 1 and " on " not in cell:
        return cell, _outcome_of(row, cell)
    found: list[str] = []
    for segment in segments:
        if match := _ON_THE_BUILD.fullmatch(segment):
            if build == "release":
                found.append(match["verdict"])
        elif match := _ON_A_BUILD_PAST.fullmatch(segment):
            if build == "main":
                found.append(match["verdict"])
        else:
            msg = f"{row}: no build qualifier reads {segment!r}"
            raise LedgerError(msg)
    if len(found) != 1:
        msg = f"{row}: {cell!r} gives the {build} build {len(found)} verdicts"
        raise LedgerError(msg)
    return found[0], _outcome_of(row, found[0])


def ledger_cells(ledger_text: str) -> dict[str, str]:
    """Return each row of the btclib-wallet ledger, with its cell.

    :param ledger_text: `TF2.md`'s own text.
    :raises LedgerError: where the section is missing, where a row does
        not have the table's own width, or where two rows share a first
        cell.
    """
    if _SECTION not in ledger_text:
        msg = f"no {_SECTION!r} section"
        raise LedgerError(msg)
    section = ledger_text.split(_SECTION, 1)[1].split("\n## ", 1)[0]
    cells: dict[str, str] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        row = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(row) != 2:
            msg = f"a row of {len(row)} cells, not two: {line!r}"
            raise LedgerError(msg)
        if row[0] in cells:
            msg = f"two rows read {row[0]}"
            raise LedgerError(msg)
        cells[row[0]] = row[1]
    return cells


def _outcome(statuses: list[str]) -> str:
    """Return a row's outcome from its testcases' own statuses."""
    if "fail" in statuses:
        return "fail"
    return "skip" if "skip" in statuses else "pass"


def parse_junit(path: Path) -> dict[str, str]:
    """Return `{"classname::name": status}` for every testcase a report holds.

    :param path: the JUnit XML report's own path.
    :returns: `"fail"` where a `<failure>` or an `<error>` child is
        present, `"skip"` where a `<skipped>` one is, `"pass"` otherwise.
        Pytest writes a second `<testcase>` of the same name for a
        teardown error, so the outcomes of one name are merged and the
        worst stands.
    """
    root = ET.parse(path).getroot()  # noqa: S314
    results: dict[str, str] = {}
    for case in root.iter("testcase"):
        key = f"{case.get('classname')}::{case.get('name')}"
        if case.find("failure") is not None or case.find("error") is not None:
            status = "fail"
        elif case.find("skipped") is not None:
            status = "skip"
        else:
            status = "pass"
        results[key] = _outcome([results[key], status]) if key in results else status
    return results


def row_of(key: str) -> str | None:
    """Return the ledger row a testcase is, from `_ROWS`.

    :param key: `parse_junit`'s own `classname::name`.
    :returns: the row's first cell, or None where `_ROWS` names neither
        the function nor its module.
    """
    classname, name = key.split("::", 1)
    stem = classname.rsplit(".", 1)[-1].removesuffix(_SUFFIX)
    function = name.split("[", 1)[0]
    return _ROWS.get(f"{stem}::{function}", _ROWS.get(stem))


def moves(ledger_text: str, results: dict[str, str], build: str) -> list[str]:
    """Return one report line per row, or testcase, that moved.

    :param ledger_text: `TF2.md`'s own text.
    :param results: `parse_junit`'s reading of the report.
    :param build: `"release"` or `"main"`, the build the report ran.
    :returns: the lines, one per row whose outcome does not agree with
        its cell's verdict for that build, per row no testcase of the
        report reached, and per testcase `_ROWS` has no row for.
    :raises LedgerError: where a cell is in no shape the table defines.
    """
    by_row: dict[str, dict[str, str]] = {}
    lines: list[str] = []
    for key, status in sorted(results.items()):
        row = row_of(key)
        if row is None:
            lines.append(
                f"- **no row**: `{key}` is a testcase `_ROWS` names no row for"
            )
        else:
            by_row.setdefault(row, {})[key.split("::", 1)[1]] = status
    for row, cell in ledger_cells(ledger_text).items():
        verdict, wanted = expected(row, cell, build)
        cases = by_row.get(row)
        if not cases:
            lines.append(f"- **missing**: {row} -- no testcase of this report is its")
            continue
        outcome = _outcome(list(cases.values()))
        if outcome in _AGREES[wanted]:
            continue
        names = ", ".join(
            f"`{name}`" for name, status in cases.items() if status == outcome
        )
        lines.append(
            f"- **{_KIND[wanted, outcome]}**: {row} -- TF2.md reads {verdict};"
            f" this run's outcome is {outcome}: {names}"
        )
    return lines


def flaky_passes(ledger_text: str, results: dict[str, str], build: str) -> list[str]:
    """Return one report line per **flaky** row this run passed.

    :param ledger_text: `TF2.md`'s own text.
    :param results: `parse_junit`'s reading of the report.
    :param build: `"release"` or `"main"`, the build the report ran.
    :returns: the lines, none of them a move.
    :raises LedgerError: where a cell is in no shape the table defines.
    """
    lines: list[str] = []
    for row, cell in ledger_cells(ledger_text).items():
        verdict, wanted = expected(row, cell, build)
        statuses = [status for key, status in results.items() if row_of(key) == row]
        if wanted == "flaky" and statuses and _outcome(statuses) == "pass":
            lines.append(
                f"- **flaky passed**: {row} -- TF2.md reads {verdict}; its"
                " section says when this moves to pass"
            )
    return lines


def main() -> int:
    """Compare the report with the ledger, and print what moved.

    It exits 1 where anything moved, and 0 where nothing did. A usage
    error is the only way this exits 2.
    """
    args = sys.argv[1:]
    if len(args) != 3 or args[2] not in BUILDS:
        print(
            f"usage: {Path(sys.argv[0]).name} <TF2.md> <junit.xml> <release|main>",
            file=sys.stderr,
        )
        return 2
    ledger_path, report_path, build = Path(args[0]), Path(args[1]), args[2]
    ledger_text = ledger_path.read_text(encoding="utf-8")
    results = parse_junit(report_path)
    lines = moves(ledger_text, results, build)
    passes = flaky_passes(ledger_text, results, build)
    print(f"### TF2.md's btclib-wallet ledger against the {build} build")
    print()
    if lines:
        print("Moved since TF2.md:")
        print()
        for line in lines:
            print(line)
    else:
        print("Nothing moved: every row agrees with TF2.md.")
    if passes:
        print()
        print("### Flaky rows this run passed, not moves")
        print()
        for line in passes:
            print(line)
    return 1 if lines else 0


if __name__ == "__main__":
    sys.exit(main())
