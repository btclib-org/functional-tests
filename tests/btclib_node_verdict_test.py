# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Tests for the btclib-node ledger comparison of `.github/scripts`.

The script is loaded by path, `.github/scripts` being no package, the
same idiom `tf2_master_verdict_test.py` uses for its own script. Most
tests read a small ledger written here; the census tests at the end read
this tree's own `TF2.md` and `tests/integration`, which is what holds the
script's row table to the tree.
"""

from __future__ import annotations

import ast
import importlib.util
import runpy
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).parents[1]
_SCRIPT = _ROOT / ".github" / "scripts" / "btclib_node_verdict.py"
_LEDGER_PATH = _ROOT / "TF2.md"
_INTEGRATION = _ROOT / "tests" / "integration"

_ISS = "[ISS btclib-node#9](https://github.com/btclib-org/btclib-node/issues/9)"
_ISS2 = "[ISS btclib-node#10](https://github.com/btclib-org/btclib-node/issues/10)"

_PKG = "tests.integration"


@pytest.fixture
def verdict(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return the script, imported by path, registered before it runs."""
    spec = importlib.util.spec_from_file_location("btclib_node_verdict", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "btclib_node_verdict", module)
    spec.loader.exec_module(module)
    return module


def _ledger(rows: list[tuple[str, str]]) -> str:
    """Return a ledger whose per-test table holds each `(row, cell)` given."""
    lines = [
        "## The per-test ledger",
        "",
        "| Core test | pin | read at | bitcoind | btclib-node |",
        "| --- | --- | --- | --- | --- |",
    ]
    lines += [
        f"| {row} | `0d1301b47a35` | 2026-03-24 | pass | {cell} |" for row, cell in rows
    ]
    lines += ["", "## Node-linking", "", "| `not_a_row.py` | x | x | x | x | x |"]
    return "\n".join(lines) + "\n"


_TIMEOUT = (
    '<error message="failed on teardown with &quot;TimeoutError: node process'
    ' ignored terminate for 30.0s and was killed&quot;">traceback</error>'
)


def _junit(
    cases: list[tuple[str, str, str]], timed_out: frozenset[str] = frozenset()
) -> str:
    """Build a JUnit report: one `(module stem, name, outcome)` per testcase.

    A testcase named in `timed_out` also carries the teardown timeout.
    """
    inner = {
        "pass": "",
        "fail": '<failure message="boom">traceback</failure>',
        "skip": '<skipped message="node does not declare mine"/>',
    }
    bodies = "".join(
        f'<testcase classname="{_PKG}.{stem}_btclib_node_test" name="{name}"'
        f' time="0.01">{inner[outcome]}{_TIMEOUT if name in timed_out else ""}</testcase>'
        for stem, name, outcome in cases
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest">{bodies}</testsuite></testsuites>'
    )


def _results(cases: list[tuple[str, str, str]]) -> dict[str, str]:
    """Return what `parse_junit` reads from `_junit(cases)`, without a file."""
    return {
        f"{_PKG}.{stem}_btclib_node_test::{name}": outcome
        for stem, name, outcome in cases
    }


@pytest.mark.parametrize(
    "cell,outcome",
    [
        ("pass", "pass"),
        ("skip", "skip"),
        ("skip (blk)", "skip"),
        (f"fail ({_ISS})", "fail"),
        (f"flaky ({_ISS})", "flaky"),
        ("not ported", "fail"),
    ],
)
def test_expected_reads_a_single_verdict_for_both_builds(
    verdict: ModuleType, cell: str, outcome: str
) -> None:
    """An unqualified verdict is every build's."""
    for build in verdict.BUILDS:
        assert verdict.expected("`x.py`", cell, build) == verdict.Expected(
            cell, outcome
        )


def test_expected_reads_bitcoind_only_as_no_verdict(verdict: ModuleType) -> None:
    """A **bitcoind only** cell names no btclib-node test on either build."""
    for build in verdict.BUILDS:
        assert verdict.expected("`x.py`", "bitcoind only", build) is None


def test_expected_reads_one_verdict_per_build(verdict: ModuleType) -> None:
    """`on the build` is the release's, `on a build past` is main's."""
    cell = f"skip (mine) on the build; not ported on a build past {_ISS}"
    assert verdict.expected("`x.py`", cell, "release").outcome == "skip"
    assert verdict.expected("`x.py`", cell, "main") == verdict.Expected(
        "not ported", "fail"
    )


def test_expected_refuses_a_segment_bounded_by_a_second_issue(
    verdict: ModuleType,
) -> None:
    """`and before` is no qualifier: a cell carries one issue per segment."""
    cell = f"skip on the build; skip on a build past {_ISS} and before {_ISS2}"
    with pytest.raises(verdict.LedgerError, match="no build qualifier reads"):
        verdict.expected("`x.py`", cell, "main")


def test_expected_reads_a_failing_verdict_on_the_build(verdict: ModuleType) -> None:
    """A verdict carrying an issue link is read whole before `on the build`."""
    cell = f"fail ({_ISS}) on the build; pass on a build past {_ISS}"
    assert verdict.expected("`x.py`", cell, "release") == verdict.Expected(
        f"fail ({_ISS})", "fail"
    )
    assert verdict.expected("`x.py`", cell, "main").outcome == "pass"


@pytest.mark.parametrize(
    "cell",
    [
        "passes",
        "fail",
        "fail (btclib-node#9)",
        "flaky",
        "skip ()",
        "skip (two words)",
        "",
    ],
)
def test_expected_refuses_an_unrecognised_verdict(
    verdict: ModuleType, cell: str
) -> None:
    """A verdict the ledger does not define is refused, never read as a pass."""
    with pytest.raises(verdict.LedgerError, match="no verdict reads"):
        verdict.expected("`x.py`", cell, "release")


def test_expected_refuses_an_unrecognised_qualifier(verdict: ModuleType) -> None:
    """A segment qualified some other way names the segment it cannot read."""
    cell = f"skip on the build; pass on a later build {_ISS}"
    with pytest.raises(verdict.LedgerError, match="no build qualifier reads"):
        verdict.expected("`x.py`", cell, "release")


def test_expected_refuses_an_unqualified_segment_beside_a_qualified_one(
    verdict: ModuleType,
) -> None:
    """Once one segment names its build, every segment must."""
    with pytest.raises(verdict.LedgerError, match="no build qualifier reads 'pass'"):
        verdict.expected("`x.py`", "skip on the build; pass", "main")


@pytest.mark.parametrize(
    "cell,build",
    [
        (f"pass on a build past {_ISS}", "release"),
        ("skip on the build", "main"),
        ("skip on the build; pass on the build", "release"),
        (f"skip on a build past {_ISS}; pass on a build past {_ISS2}", "main"),
    ],
)
def test_expected_refuses_a_cell_giving_a_build_no_single_verdict(
    verdict: ModuleType, cell: str, build: str
) -> None:
    """A build given no verdict, or two, is refused rather than guessed."""
    with pytest.raises(verdict.LedgerError, match=f"gives the {build} build"):
        verdict.expected("`x.py`", cell, build)


def test_ledger_cells_reads_the_per_test_table_alone(verdict: ModuleType) -> None:
    """Rows of the per-test table, and not a later section's table."""
    text = _ledger([("`a.py`", "pass"), ("`a.py` (log)", "skip")])
    assert verdict.ledger_cells(text) == {"`a.py`": "pass", "`a.py` (log)": "skip"}


def test_ledger_cells_refuses_a_ledger_with_no_table(verdict: ModuleType) -> None:
    """No per-test ledger section is refused, not read as no rows."""
    with pytest.raises(verdict.LedgerError, match="no '## The per-test ledger'"):
        verdict.ledger_cells("# The tf2 ledger\n")


def test_ledger_cells_refuses_a_row_of_the_wrong_width(verdict: ModuleType) -> None:
    """A `|` inside a cell would shift the btclib-node column; refused."""
    text = _ledger([("`a.py`", "pass | extra")])
    with pytest.raises(verdict.LedgerError, match="a row of 6 cells"):
        verdict.ledger_cells(text)


def test_ledger_cells_refuses_two_rows_of_one_name(verdict: ModuleType) -> None:
    """Two rows sharing a first cell leave a testcase two rows to be."""
    text = _ledger([("`a.py`", "pass"), ("`a.py`", "skip")])
    with pytest.raises(verdict.LedgerError, match="two rows read `a.py`"):
        verdict.ledger_cells(text)


def test_parse_junit_classifies_fail_error_skip_and_pass(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """A failure or an error is fail, a skip is skip, else pass."""
    report = tmp_path / "integration.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        '<testcase classname="m" name="a"/>'
        '<testcase classname="m" name="b"><failure message="x"/></testcase>'
        '<testcase classname="m" name="c"><error message="x"/></testcase>'
        '<testcase classname="m" name="d"><skipped message="x"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert verdict.parse_junit(report) == {
        "m::a": "pass",
        "m::b": "fail",
        "m::c": "fail",
        "m::d": "skip",
    }


def test_locate_drops_the_suffix_and_the_parameter_id(verdict: ModuleType) -> None:
    """A parametrized testcase is its function's, `[id]` and all."""
    key = f"{_PKG}.rpc_users_btclib_node_test::test_malformed[-rpcauth=foo]"
    assert verdict.locate(key) == ("rpc_users", "test_malformed")


def test_row_of_prefers_the_function_to_its_module(verdict: ModuleType) -> None:
    """A function entry wins over its module's; an unknown pair has no row."""
    assert verdict.row_of("rpc_users", "test_anything") == "`rpc_users.py`"
    assert (
        verdict.row_of("rpc_users", "test_norpcauth_disables_previous_rpcauth")
        == "`rpc_users.py` (`-norpcauth`)"
    )
    assert verdict.row_of("nowhere", "test_a") is None


def _patch_rows(verdict: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the row table with a small one the tests below reason about."""
    monkeypatch.setattr(
        verdict,
        "_ROWS",
        {"a": "`a.py`", "b": "`b.py`", "c": "`c.py`", "d": "`d.py`"},
    )
    monkeypatch.setattr(verdict, "_NO_ROW", frozenset({"harness"}))


_ROWS_LEDGER = _ledger(
    [
        ("`a.py`", "pass"),
        ("`b.py`", f"fail ({_ISS})"),
        ("`c.py`", "skip (blk)"),
        ("`d.py`", f"skip (mine) on the build; not ported on a build past {_ISS}"),
        ("`e.py`", "bitcoind only"),
    ]
)


def test_moves_is_empty_where_every_row_agrees(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skip row may hold a passing test; a fail row needs one failure."""
    _patch_rows(verdict, monkeypatch)
    results = _results(
        [
            ("a", "test_a", "pass"),
            ("b", "test_b1", "fail"),
            ("b", "test_b2", "pass"),
            ("b", "test_b3", "skip"),
            ("c", "test_c1", "skip"),
            ("c", "test_c2", "pass"),
            ("d", "test_d", "skip"),
            ("harness", "test_h1", "pass"),
            ("harness", "test_h2", "skip"),
        ]
    )
    assert verdict.moves(_ROWS_LEDGER, results, "release") == []


def test_moves_reads_each_build_against_its_own_verdict(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same failing stub agrees with main's cell and not the release's."""
    _patch_rows(verdict, monkeypatch)
    results = _results(
        [
            ("a", "test_a", "pass"),
            ("b", "test_b", "fail"),
            ("c", "test_c", "skip"),
            ("d", "test_d", "fail"),
        ]
    )
    assert verdict.moves(_ROWS_LEDGER, results, "main") == []
    assert verdict.moves(_ROWS_LEDGER, results, "release") == [
        (
            "- **ran, and failed**: `d.py` -- TF2.md reads skip (mine); this run's"
            " outcome is fail: `test_d`"
        )
    ]


@pytest.mark.parametrize(
    "cases,kind",
    [
        ([("a", "test_a", "fail")], "regression"),
        ([("a", "test_a", "pass"), ("a", "test_a2", "skip")], "skipped"),
        ([("b", "test_b", "pass")], "fixed"),
        ([("b", "test_b", "skip")], "skipped"),
        ([("c", "test_c", "pass")], "ran"),
        ([("c", "test_c", "fail")], "ran, and failed"),
    ],
)
def test_moves_names_each_kind_of_move(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    cases: list[tuple[str, str, str]],
    kind: str,
) -> None:
    """Every pair of a ledger outcome and a different run outcome is named."""
    _patch_rows(verdict, monkeypatch)
    rows = {stem for stem, _, _ in cases}
    agreeing = [
        case
        for case in (
            ("a", "test_a", "pass"),
            ("b", "test_b", "fail"),
            ("c", "test_c", "skip"),
            ("d", "test_d", "skip"),
        )
        if case[0] not in rows
    ]
    lines = verdict.moves(_ROWS_LEDGER, _results(cases + agreeing), "release")
    assert len(lines) == 1
    assert lines[0].startswith(f"- **{kind}**: ")


@pytest.mark.parametrize("outcome", ["pass", "fail"])
def test_moves_reads_a_flaky_row_as_agreeing_with_a_pass_or_a_fail(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """A **flaky** row's race goes either way, and neither way is a move."""
    _patch_rows(verdict, monkeypatch)
    text = _ledger([("`a.py`", f"flaky ({_ISS})")])
    results = _results([("a", "test_a", outcome)])
    for build in verdict.BUILDS:
        assert verdict.moves(text, results, build) == []


def test_moves_names_a_flaky_row_that_skipped(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A skip is no outcome of the race, so it is a move."""
    _patch_rows(verdict, monkeypatch)
    text = _ledger([("`a.py`", f"flaky ({_ISS})")])
    results = _results([("a", "test_a", "skip")])
    assert verdict.moves(text, results, "release") == [
        (
            f"- **skipped**: `a.py` -- TF2.md reads flaky ({_ISS}); this run's"
            " outcome is skip: `test_a`"
        )
    ]


_FLAKY_PASSED = (
    f"- **flaky passed**: `a.py` -- TF2.md reads flaky ({_ISS}); its legend"
    " says when this moves to pass"
)


def test_flaky_passes_lists_only_a_flaky_row_this_run_passed(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A flaky row that failed, skipped or ran nothing is not listed."""
    _patch_rows(verdict, monkeypatch)
    flaky = f"flaky ({_ISS})"
    text = _ledger(
        [
            ("`a.py`", flaky),
            ("`b.py`", flaky),
            ("`c.py`", flaky),
            ("`d.py`", flaky),
            ("`e.py`", "pass"),
            ("`f.py`", "bitcoind only"),
        ]
    )
    results = _results(
        [
            ("a", "test_a", "pass"),
            ("b", "test_b1", "pass"),
            ("b", "test_b2", "fail"),
            ("c", "test_c", "skip"),
        ]
    )
    for build in verdict.BUILDS:
        assert verdict.flaky_passes(text, results, build) == [_FLAKY_PASSED]


def test_flaky_passes_reads_each_build_against_its_own_verdict(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build past the issue reads pass, so its pass is no flaky row's."""
    _patch_rows(verdict, monkeypatch)
    text = _ledger(
        [("`a.py`", f"flaky ({_ISS}) on the build; pass on a build past {_ISS}")]
    )
    results = _results([("a", "test_a", "pass")])
    assert verdict.flaky_passes(text, results, "release") == [_FLAKY_PASSED]
    assert verdict.flaky_passes(text, results, "main") == []


def test_moves_names_only_the_testcases_that_decided_the_outcome(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A regression names the failing testcase, not its passing sibling."""
    _patch_rows(verdict, monkeypatch)
    results = _results(
        [
            ("a", "test_ok", "pass"),
            ("a", "test_bad[x]", "fail"),
            ("b", "test_b", "fail"),
            ("c", "test_c", "skip"),
            ("d", "test_d", "skip"),
        ]
    )
    assert verdict.moves(_ROWS_LEDGER, results, "release") == [
        (
            "- **regression**: `a.py` -- TF2.md reads pass; this run's outcome is"
            " fail: `test_bad[x]`"
        )
    ]


def test_moves_names_a_row_no_testcase_reached(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row whose tests the report lacks is named, not read as agreeing."""
    _patch_rows(verdict, monkeypatch)
    results = _results(
        [("a", "test_a", "pass"), ("b", "test_b", "fail"), ("c", "test_c", "skip")]
    )
    assert verdict.moves(_ROWS_LEDGER, results, "release") == [
        "- **missing**: `d.py` -- no testcase of this report is its"
    ]


def test_moves_names_a_testcase_with_no_row(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A testcase the table does not know is named whatever its outcome."""
    _patch_rows(verdict, monkeypatch)
    results = _results(
        [
            ("a", "test_a", "pass"),
            ("b", "test_b", "fail"),
            ("c", "test_c", "skip"),
            ("d", "test_d", "skip"),
            ("new", "test_n", "pass"),
            ("harness", "test_h", "fail"),
        ]
    )
    assert verdict.moves(_ROWS_LEDGER, results, "release") == [
        (
            f"- **no row**: `{_PKG}.harness_btclib_node_test::test_h` fails, and no"
            " ledger row covers it"
        ),
        (
            f"- **no row**: `{_PKG}.new_btclib_node_test::test_n` is a testcase"
            " `_ROWS` names no row for"
        ),
    ]


def test_moves_refuses_an_unreadable_cell(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable cell stops the comparison rather than passing its row."""
    _patch_rows(verdict, monkeypatch)
    with pytest.raises(verdict.LedgerError):
        verdict.moves(_ledger([("`a.py`", "works")]), {}, "release")


def _run(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cases: list[tuple[str, str, str]],
    build: str,
    timed_out: frozenset[str] = frozenset(),
    ledger_text: str = _ROWS_LEDGER,
) -> int:
    """Write the ledger and a report of `cases`, and run `main` on them."""
    _patch_rows(verdict, monkeypatch)
    ledger = tmp_path / "TF2.md"
    ledger.write_text(ledger_text, encoding="utf-8")
    report = tmp_path / "integration.xml"
    report.write_text(_junit(cases, timed_out), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), str(report), build])
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


_CLEAN = [
    ("a", "test_a", "pass"),
    ("b", "test_b", "fail"),
    ("c", "test_c", "skip"),
    ("d", "test_d", "fail"),
]


def test_main_reports_a_passed_call_with_a_teardown_timeout_apart(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The call passed, so the row agrees: no move, a heading, exit 0."""
    result = _run(verdict, monkeypatch, tmp_path, _CLEAN, "main", frozenset({"test_a"}))
    assert result == 0
    assert capsys.readouterr().out == (
        "### TF2.md's btclib-node column against the main build\n\n"
        "Nothing moved: every row agrees with TF2.md.\n\n"
        "### Teardown timeouts (btclib-org/btclib-node#1274), not verdicts\n\n"
        f"- `{_PKG}.a_btclib_node_test::test_a`: the node ignored terminate;"
        " its call's outcome stands\n"
    )


def test_main_reports_a_failed_call_with_a_teardown_timeout_twice(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The call failed: the row moves, and the timeout is still listed."""
    cases = [("a", "test_a", "fail"), *_CLEAN[1:]]
    result = _run(verdict, monkeypatch, tmp_path, cases, "main", frozenset({"test_a"}))
    assert result == 1
    out = capsys.readouterr().out
    assert "- **regression**: `a.py`" in out
    assert "### Teardown timeouts (btclib-org/btclib-node#1274), not verdicts" in out
    assert f"- `{_PKG}.a_btclib_node_test::test_a`" in out


def test_main_says_nothing_moved(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A clean pass prints the heading and one line, and exits 0."""
    assert _run(verdict, monkeypatch, tmp_path, _CLEAN, "main") == 0
    out = capsys.readouterr().out
    assert out == (
        "### TF2.md's btclib-node column against the main build\n\n"
        "Nothing moved: every row agrees with TF2.md.\n"
    )


def test_main_lists_a_flaky_row_that_passed_apart_and_exits_0(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A flaky row that passed is no move: a heading of its own, exit 0."""
    text = _ledger([("`a.py`", f"flaky ({_ISS})")])
    cases = [("a", "test_a", "pass")]
    assert _run(verdict, monkeypatch, tmp_path, cases, "release", ledger_text=text) == 0
    assert capsys.readouterr().out == (
        "### TF2.md's btclib-node column against the release build\n\n"
        "Nothing moved: every row agrees with TF2.md.\n\n"
        "### Flaky rows this run passed, not moves\n\n"
        f"{_FLAKY_PASSED}\n"
    )


def test_parse_junit_keeps_the_call_s_outcome_under_a_teardown_timeout(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """Only the timeout's own `<error>` is set aside; any other one fails."""
    report = tmp_path / "integration.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites><testsuite name="pytest">'
        f'<testcase classname="m" name="a">{_TIMEOUT}</testcase>'
        f'<testcase classname="m" name="b"><skipped message="x"/>{_TIMEOUT}</testcase>'
        '<testcase classname="m" name="c"><error message="failed on teardown with'
        ' &quot;OSError: other&quot;"/></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert verdict.parse_junit(report) == {
        "m::a": "pass",
        "m::b": "skip",
        "m::c": "fail",
    }
    assert verdict.teardown_timeouts(report) == ["m::a", "m::b"]


def test_main_prints_a_move_and_exits_1(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A row that now passes is a move too, and fails the step."""
    cases = [
        ("a", "test_a", "pass"),
        ("b", "test_b", "pass"),
        ("c", "test_c", "skip"),
        ("d", "test_d", "skip"),
    ]
    assert _run(verdict, monkeypatch, tmp_path, cases, "release") == 1
    out = capsys.readouterr().out
    assert "Moved since TF2.md:" in out
    assert "- **fixed**: `b.py` -- TF2.md reads fail (" in out


# the census: the script's own table held to this tree's ledger and tests


def _test_functions() -> dict[str, list[str]]:
    """Return each `*_btclib_node_test.py` module's stem, with its tests."""
    found: dict[str, list[str]] = {}
    for module in sorted(_INTEGRATION.glob("*_btclib_node_test.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        found[module.stem.removesuffix("_btclib_node_test")] = [
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
    """Every `btclib-node` cell of `TF2.md` gives each build one verdict."""
    cells = verdict.ledger_cells(_LEDGER_PATH.read_text(encoding="utf-8"))
    for row, cell in cells.items():
        for build in verdict.BUILDS:
            verdict.expected(row, cell, build)


def test_every_test_function_is_a_row_or_a_harness_test(verdict: ModuleType) -> None:
    """A new `*_btclib_node_test.py` test with no row fails here, not in CI."""
    unrowed = [
        f"{stem}::{function}"
        for stem, functions in _test_functions().items()
        for function in functions
        if verdict.row_of(stem, function) is None and stem not in verdict._NO_ROW
    ]
    assert not unrowed


def test_every_table_entry_names_a_test_that_exists(verdict: ModuleType) -> None:
    """A `_ROWS` or `_NO_ROW` entry outliving its test is a stale entry."""
    functions = _test_functions()
    for key in verdict._ROWS:
        stem, _, function = key.partition("::")
        assert stem in functions, key
        assert not function or function in functions[stem], key
    assert set(functions) >= verdict._NO_ROW
    assert not verdict._NO_ROW & {key.partition("::")[0] for key in verdict._ROWS}


def test_every_row_is_some_test_and_every_test_row_a_row(
    verdict: ModuleType,
) -> None:
    """The table and the ledger's btclib-node column name the same rows.

    A module entry every one of whose functions has an entry of its own
    reaches no testcase, so the rows reached are counted from the test
    functions rather than from the table's own values.
    """
    cells = verdict.ledger_cells(_LEDGER_PATH.read_text(encoding="utf-8"))
    compared = {row for row, cell in cells.items() if cell != "bitcoind only"}
    reached = {
        verdict.row_of(stem, function)
        for stem, functions in _test_functions().items()
        for function in functions
        if stem not in verdict._NO_ROW
    }
    assert reached == compared
    assert set(verdict._ROWS.values()) == compared


_REAL_TESTS = """
import pytest

@pytest.fixture
def node():
    yield
    raise TimeoutError("node process ignored terminate for 30.0s and was killed")

def test_failed(node):
    assert False

def test_skipped(node):
    pytest.skip("not declared")

def test_passed(node):
    pass
"""


def test_parse_junit_reads_what_pytest_writes_for_a_teardown_timeout(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """A call's own outcome stands, in the report pytest itself writes.

    Pytest writes a second `<testcase>` of the same name for the teardown
    error after a call that failed or skipped, so the report is generated
    here rather than written by hand.
    """
    (tmp_path / "test_real.py").write_text(_REAL_TESTS, encoding="utf-8")
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    report = tmp_path / "integration.xml"
    subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "no:cacheprovider",
            "-p",
            "no:randomly",
            "--junitxml",
            str(report),
            str(tmp_path / "test_real.py"),
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
    )
    assert verdict.parse_junit(report) == {
        "test_real::test_failed": "fail",
        "test_real::test_skipped": "skip",
        "test_real::test_passed": "pass",
    }
    assert verdict.teardown_timeouts(report) == [
        "test_real::test_failed",
        "test_real::test_passed",
        "test_real::test_skipped",
    ]
