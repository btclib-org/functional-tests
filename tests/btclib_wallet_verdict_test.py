# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Tests for the btclib-wallet ledger comparison of `.github/scripts`.

The script is loaded by path, `.github/scripts` being no package, as
`btclib_node_verdict_test.py` loads its own. Most tests read a small
ledger written here; the census tests at the end read this tree's own
`TF2.md` and `tests/integration`, which holds the script's row table to
the tree.
"""

from __future__ import annotations

import ast
import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).parents[1]
_SCRIPT = _ROOT / ".github" / "scripts" / "btclib_wallet_verdict.py"
_LEDGER_PATH = _ROOT / "TF2.md"
_INTEGRATION = _ROOT / "tests" / "integration"

_ISS = "[ISS btclib-wallet#9](https://github.com/btclib-org/btclib-wallet/issues/9)"

_PKG = "tests.integration"


@pytest.fixture
def verdict(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return the script, imported by path, registered before it runs."""
    spec = importlib.util.spec_from_file_location("btclib_wallet_verdict", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "btclib_wallet_verdict", module)
    spec.loader.exec_module(module)
    return module


def _ledger(rows: list[tuple[str, str]]) -> str:
    """Return a ledger whose btclib-wallet table holds each `(row, cell)`."""
    lines = [
        "## The per-test ledger",
        "",
        "| `not_this_table.py` | x | x | x | x |",
        "",
        "## The btclib-wallet ledger",
        "",
        "| Core test | btclib-wallet |",
        "| --- | --- |",
    ]
    lines += [f"| {row} | {cell} |" for row, cell in rows]
    lines += ["", "## After", "", "| `not_a_row.py` | x |"]
    return "\n".join(lines) + "\n"


def _junit(cases: list[tuple[str, str, str]]) -> str:
    """Build a JUnit report: one `(module stem, name, outcome)` per testcase."""
    inner = {
        "pass": "",
        "fail": '<failure message="boom">traceback</failure>',
        "error": '<error message="boom">traceback</error>',
        "skip": '<skipped message="wallet does not declare wallet_reload"/>',
    }
    body = "".join(
        f'<testcase classname="{_PKG}.{stem}_btclib_wallet_test" name="{name}">'
        f"{inner[outcome]}</testcase>"
        for stem, name, outcome in cases
    )
    return f'<testsuites><testsuite name="pytest">{body}</testsuite></testsuites>'


def _results(cases: list[tuple[str, str, str]]) -> dict[str, str]:
    """Return `parse_junit`'s shape for `cases`, without writing a file."""
    return {
        f"{_PKG}.{stem}_btclib_wallet_test::{name}": outcome
        for stem, name, outcome in cases
    }


@pytest.mark.parametrize(
    "cell, outcome",
    [
        ("pass", "pass"),
        (f"fail ({_ISS})", "fail"),
        (f"stub ({_ISS})", "fail"),
        (f"skip (wallet_reload, {_ISS})", "skip"),
        (f"flaky ({_ISS})", "flaky"),
    ],
)
def test_expected_reads_each_verdict_for_both_builds(
    verdict: ModuleType, cell: str, outcome: str
) -> None:
    """A single verdict is true of every build."""
    for build in verdict.BUILDS:
        assert verdict.expected("`a.py`", cell, build) == (cell, outcome)


def test_expected_reads_one_verdict_per_build(verdict: ModuleType) -> None:
    """The release's segment, then `main`'s."""
    cell = f"fail ({_ISS}) on the build; pass on a build past {_ISS}"
    assert verdict.expected("`a.py`", cell, "release") == (f"fail ({_ISS})", "fail")
    assert verdict.expected("`a.py`", cell, "main") == ("pass", "pass")


@pytest.mark.parametrize(
    "cell",
    ["skip", "skip (wallet_reload)", "fail", "not ported", "pass, mostly"],
)
def test_expected_refuses_an_unrecognised_verdict(
    verdict: ModuleType, cell: str
) -> None:
    """A skip names a capability and an issue; a fail and a stub, an issue."""
    with pytest.raises(verdict.LedgerError, match="no verdict reads"):
        verdict.expected("`a.py`", cell, "release")


def test_expected_refuses_an_unrecognised_qualifier(verdict: ModuleType) -> None:
    """A segment naming no build the table defines is refused."""
    with pytest.raises(verdict.LedgerError, match="no build qualifier"):
        verdict.expected("`a.py`", "pass on the release; pass", "release")


def test_expected_refuses_a_cell_giving_a_build_no_single_verdict(
    verdict: ModuleType,
) -> None:
    """Two segments for one build, or none, is no verdict for it."""
    cell = "pass on the build; pass on the build"
    with pytest.raises(verdict.LedgerError, match="2 verdicts"):
        verdict.expected("`a.py`", cell, "release")
    with pytest.raises(verdict.LedgerError, match="0 verdicts"):
        verdict.expected("`a.py`", cell, "main")


def test_ledger_cells_reads_its_own_table_alone(verdict: ModuleType) -> None:
    """Neither the per-test table before it nor a table after it is read."""
    text = _ledger([("`a.py`", "pass"), ("`b.py` (variant)", f"fail ({_ISS})")])
    assert verdict.ledger_cells(text) == {
        "`a.py`": "pass",
        "`b.py` (variant)": f"fail ({_ISS})",
    }


def test_ledger_cells_refuses_a_ledger_with_no_table(verdict: ModuleType) -> None:
    """A missing section is refused, not read as no rows."""
    with pytest.raises(verdict.LedgerError, match="no '## The btclib-wallet"):
        verdict.ledger_cells("## The per-test ledger\n")


def test_ledger_cells_refuses_a_row_of_the_wrong_width(verdict: ModuleType) -> None:
    """A row of other than two cells is refused."""
    text = _ledger([("`a.py`", "pass | pass")])
    with pytest.raises(verdict.LedgerError, match="not two"):
        verdict.ledger_cells(text)


def test_ledger_cells_refuses_two_rows_of_one_name(verdict: ModuleType) -> None:
    """Two rows of one first cell are refused."""
    text = _ledger([("`a.py`", "pass"), ("`a.py`", "pass")])
    with pytest.raises(verdict.LedgerError, match="two rows read"):
        verdict.ledger_cells(text)


def test_parse_junit_classifies_and_merges(verdict: ModuleType, tmp_path: Path) -> None:
    """A failure or an error fails, a skip skips; one name's worst stands."""
    report = tmp_path / "integration.xml"
    cases = [
        ("a", "test_pass", "pass"),
        ("a", "test_fail", "fail"),
        ("a", "test_error", "error"),
        ("a", "test_skip", "skip"),
        ("a", "test_twice", "pass"),
        ("a", "test_twice", "error"),
    ]
    report.write_text(_junit(cases), encoding="utf-8")
    key = f"{_PKG}.a_btclib_wallet_test::"
    assert verdict.parse_junit(report) == {
        f"{key}test_pass": "pass",
        f"{key}test_fail": "fail",
        f"{key}test_error": "fail",
        f"{key}test_skip": "skip",
        f"{key}test_twice": "fail",
    }


def test_row_of_prefers_the_function_to_its_module(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A function's entry wins; a parameter id is dropped; else the module's."""
    rows = {"m": "`m.py`", "m::test_b": "`m.py` (b)"}
    monkeypatch.setattr(verdict, "_ROWS", rows)
    key = f"{_PKG}.m_btclib_wallet_test::"
    assert verdict.row_of(f"{key}test_a") == "`m.py`"
    assert verdict.row_of(f"{key}test_b[x]") == "`m.py` (b)"
    assert verdict.row_of(f"{_PKG}.z_btclib_wallet_test::test_a") is None


_TABLE = {"a": "`a.py`", "b": "`b.py`", "c": "`c.py`"}

_TABLE_LEDGER = _ledger(
    [
        ("`a.py`", "pass"),
        ("`b.py`", f"fail ({_ISS}) on the build; pass on a build past {_ISS}"),
        ("`c.py`", f"skip (wallet_reload, {_ISS})"),
    ]
)


def test_moves_is_empty_where_every_row_agrees(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each build read against its own verdict: nothing moved."""
    monkeypatch.setattr(verdict, "_ROWS", _TABLE)
    release = [("a", "test_a", "pass"), ("b", "test_b", "fail"), ("c", "t", "skip")]
    main = [("a", "test_a", "pass"), ("b", "test_b", "pass"), ("c", "t", "skip")]
    assert verdict.moves(_TABLE_LEDGER, _results(release), "release") == []
    assert verdict.moves(_TABLE_LEDGER, _results(main), "main") == []


@pytest.mark.parametrize(
    "outcomes, kind",
    [
        (("fail", "fail", "skip"), "regression"),
        (("skip", "fail", "skip"), "skipped"),
        (("pass", "pass", "skip"), "fixed"),
        (("pass", "skip", "skip"), "skipped"),
        (("pass", "fail", "pass"), "ran"),
        (("pass", "fail", "fail"), "ran, and failed"),
    ],
)
def test_moves_names_each_kind_of_move(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    outcomes: tuple[str, str, str],
    kind: str,
) -> None:
    """Each pair of a verdict and another outcome is named as its kind."""
    monkeypatch.setattr(verdict, "_ROWS", _TABLE)
    cases = [
        (stem, f"test_{stem}", outcome)
        for stem, outcome in zip("abc", outcomes, strict=True)
    ]
    (line,) = verdict.moves(_TABLE_LEDGER, _results(cases), "release")
    assert line.startswith(f"- **{kind}**: ")


def test_moves_names_a_row_no_testcase_reached(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose testcases are missing from the report has moved."""
    monkeypatch.setattr(verdict, "_ROWS", _TABLE)
    cases = [("a", "test_a", "pass"), ("b", "test_b", "fail")]
    lines = verdict.moves(_TABLE_LEDGER, _results(cases), "release")
    assert lines == ["- **missing**: `c.py` -- no testcase of this report is its"]


def test_moves_names_a_testcase_with_no_row(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A testcase `_ROWS` names no row for is reported, whatever it did."""
    monkeypatch.setattr(verdict, "_ROWS", _TABLE)
    cases = [
        ("a", "test_a", "pass"),
        ("b", "test_b", "fail"),
        ("c", "test_c", "skip"),
        ("z", "test_z", "pass"),
    ]
    lines = verdict.moves(_TABLE_LEDGER, _results(cases), "release")
    key = f"{_PKG}.z_btclib_wallet_test::test_z"
    assert lines == [f"- **no row**: `{key}` is a testcase `_ROWS` names no row for"]


_FLAKY_ROWS = {"f": "`f.py`", "g": "`g.py`"}

_FLAKY_LEDGER = _ledger([("`f.py`", f"flaky ({_ISS})"), ("`g.py`", "pass")])


@pytest.mark.parametrize("outcome", ["pass", "fail"])
def test_a_flaky_row_agrees_with_a_pass_and_a_fail(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """Either outcome of a flaky row is no move."""
    monkeypatch.setattr(verdict, "_ROWS", _FLAKY_ROWS)
    cases = [("f", "test_f", outcome), ("g", "test_g", "pass")]
    assert verdict.moves(_FLAKY_LEDGER, _results(cases), "release") == []


def test_a_flaky_row_that_skipped_moved(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skip is neither outcome a flaky row expects."""
    monkeypatch.setattr(verdict, "_ROWS", _FLAKY_ROWS)
    cases = [("f", "test_f", "skip"), ("g", "test_g", "pass")]
    (line,) = verdict.moves(_FLAKY_LEDGER, _results(cases), "release")
    assert line.startswith("- **skipped**: `f.py`")


@pytest.mark.parametrize("outcome, listed", [("pass", True), ("fail", False)])
def test_flaky_passes_lists_a_flaky_row_that_passed(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch, outcome: str, listed: bool
) -> None:
    """Only a flaky row is listed, and only where it passed."""
    monkeypatch.setattr(verdict, "_ROWS", _FLAKY_ROWS)
    cases = [("f", "test_f", outcome), ("g", "test_g", "pass")]
    lines = verdict.flaky_passes(_FLAKY_LEDGER, _results(cases), "release")
    line = (
        f"- **flaky passed**: `f.py` -- TF2.md reads flaky ({_ISS}); its"
        " section says when this moves to pass"
    )
    assert lines == ([line] if listed else [])


def test_main_lists_a_flaky_pass_apart_and_exits_0(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A flaky row that passed is printed under its own heading."""
    monkeypatch.setattr(verdict, "_ROWS", _FLAKY_ROWS)
    ledger = tmp_path / "TF2.md"
    ledger.write_text(_FLAKY_LEDGER, encoding="utf-8")
    report = tmp_path / "integration.xml"
    cases = [("f", "test_f", "pass"), ("g", "test_g", "pass")]
    report.write_text(_junit(cases), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), str(report), "release"])
    assert verdict.main() == 0
    out = capsys.readouterr().out
    assert "Nothing moved" in out
    assert "### Flaky rows this run passed, not moves" in out
    assert "- **flaky passed**: `f.py`" in out


def _run(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cases: list[tuple[str, str, str]],
) -> int:
    """Write the ledger and a report of `cases`, and run `main` on them."""
    monkeypatch.setattr(verdict, "_ROWS", _TABLE)
    ledger = tmp_path / "TF2.md"
    ledger.write_text(_TABLE_LEDGER, encoding="utf-8")
    report = tmp_path / "integration.xml"
    report.write_text(_junit(cases), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), str(report), "release"])
    result: int = verdict.main()
    return result


@pytest.mark.parametrize(
    "argv",
    [["prog", "TF2.md", "integration.xml"], ["prog", "a", "b", "nightly"]],
)
def test_main_usage_error(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch, argv: list[str]
) -> None:
    """Not three arguments, or a build it does not know, is exit 2."""
    monkeypatch.setattr(sys, "argv", argv)
    assert verdict.main() == 2


def test_the_script_exits_with_main_s_own_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run as the workflow step runs it, the exit status is `main`'s."""
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert excinfo.value.code == 2


def test_main_says_nothing_moved(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A run agreeing with every row exits 0, and says so."""
    cases = [("a", "test_a", "pass"), ("b", "test_b", "fail"), ("c", "t", "skip")]
    assert _run(verdict, monkeypatch, tmp_path, cases) == 0
    out = capsys.readouterr().out
    assert "against the release build" in out
    assert "Nothing moved" in out


def test_main_prints_a_move_and_exits_1(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A row that moved is printed, and the exit status is 1."""
    cases = [("a", "test_a", "fail"), ("b", "test_b", "fail"), ("c", "t", "skip")]
    assert _run(verdict, monkeypatch, tmp_path, cases) == 1
    out = capsys.readouterr().out
    assert "Moved since TF2.md:" in out
    assert "- **regression**: `a.py`" in out


def _test_functions() -> dict[str, list[str]]:
    """Return each `*_btclib_wallet_test.py` module's stem, with its tests."""
    found: dict[str, list[str]] = {}
    for module in sorted(_INTEGRATION.glob("*_btclib_wallet_test.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        found[module.stem.removesuffix("_btclib_wallet_test")] = [
            node.name
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
        ]
    return found


def test_the_census_read_both_sides(verdict: ModuleType) -> None:
    """An empty side would make every census assertion below pass for free."""
    assert verdict.ledger_cells(_LEDGER_PATH.read_text(encoding="utf-8"))
    functions = _test_functions()
    assert functions
    assert all(functions.values())


def test_every_ledger_cell_reads_on_both_builds(verdict: ModuleType) -> None:
    """Every `btclib-wallet` cell of `TF2.md` gives each build one verdict."""
    cells = verdict.ledger_cells(_LEDGER_PATH.read_text(encoding="utf-8"))
    for row, cell in cells.items():
        for build in verdict.BUILDS:
            verdict.expected(row, cell, build)


def test_every_row_is_some_test_and_every_test_a_row(verdict: ModuleType) -> None:
    """The table, the tests and `TF2.md`'s btclib-wallet ledger agree.

    Every test function resolves to a row, every `_ROWS` entry names a
    test that exists, and the rows reached are the ledger's rows.
    """
    functions = _test_functions()
    reached = {
        verdict.row_of(f"{_PKG}.{stem}{verdict._SUFFIX}::{function}")
        for stem, names in functions.items()
        for function in names
    }
    assert None not in reached
    for key in verdict._ROWS:
        stem, _, function = key.partition("::")
        assert stem in functions, key
        assert not function or function in functions[stem], key
    cells = verdict.ledger_cells(_LEDGER_PATH.read_text(encoding="utf-8"))
    assert reached == set(cells)
    assert set(verdict._ROWS.values()) == set(cells)


def test_every_ledger_row_is_a_row_of_the_per_test_ledger(
    verdict: ModuleType,
) -> None:
    """A btclib-wallet row names a Core test the per-test ledger pins."""
    text = _LEDGER_PATH.read_text(encoding="utf-8")
    per_test = text.split("## The per-test ledger", 1)[1].split("\n## ", 1)[0]
    rows = {
        line.strip().strip("|").split("|")[0].strip()
        for line in per_test.splitlines()
        if line.startswith("| `")
    }
    assert set(verdict.ledger_cells(text)) <= rows
