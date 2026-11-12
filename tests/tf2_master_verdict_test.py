# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Tests for the master-only failure classifier of `.github/scripts`.

Its only external dependency is `gh`, called per master-only failure
for the last commit on `master` of the Core file and of each data file
it loads, and for whether such a commit is in the pin's own history.
Every test that reaches it replaces `subprocess.run`: with `FakeGh`,
which answers the way a real `gh api repos/bitcoin/bitcoin/commits` or
`gh api repos/bitcoin/bitcoin/compare` would rather than reaching
GitHub, or with `_failing_gh`'s stand-in, which fails as gh does.

The script is loaded by path, `.github/scripts` being no package, the
same idiom `tf2_ledger_census_test.py` and `tf2_ledger_test.py` use for
their own scripts.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "tf2_master_verdict.py"

_DIR = "test/functional"


@pytest.fixture
def verdict(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return the script, imported by path, registered before it runs."""
    spec = importlib.util.spec_from_file_location("tf2_master_verdict", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "tf2_master_verdict", module)
    spec.loader.exec_module(module)
    return module


class FakeGh:
    """A `subprocess.run` stand-in, answering `gh api` for commits and compare.

    `commits` maps a path to the (sha, date) `_latest_commit` should
    report for it; a path absent from it answers with no commit at all,
    matching upstream having no commit for a path `master` never held.
    `statuses` maps `"<pin>...<commit>"` to the `status` a
    comparison answers, `ahead` where it names none. Every call is
    recorded in `calls`.
    """

    def __init__(self) -> None:
        self.commits: dict[str, tuple[str, str]] = {}
        self.statuses: dict[str, str] = {}
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Record the call, and answer the path or the pair it asked about."""
        self.calls.append(list(argv))
        endpoint = argv[4]
        if "/compare/" in endpoint:
            status = self.statuses.get(endpoint.rsplit("/", 1)[1], "ahead")
            return subprocess.CompletedProcess(argv, 0, stdout=f"{status}\n")
        path = next(a.removeprefix("path=") for a in argv if a.startswith("path="))
        found = self.commits.get(path)
        commits = (
            [
                {
                    "sha": found[0],
                    "commit": {"committer": {"date": f"{found[1]}T00:00:00Z"}},
                }
            ]
            if found is not None
            else []
        )
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(commits))


@pytest.fixture
def fake_gh(verdict: ModuleType, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    """Install a `FakeGh` in place of the script's own `subprocess.run`."""
    fake = FakeGh()
    monkeypatch.setattr(verdict.subprocess, "run", fake)
    return fake


def _junit(cases: list[tuple[str, str, str]]) -> str:
    """Build a minimal JUnit XML report.

    :param cases: one `(classname, name, outcome)` triple per testcase,
        `outcome` one of `"pass"`, `"fail"` or `"skip"`.
    :returns: the report's own text.
    """
    bodies = []
    for classname, name, outcome in cases:
        inner = {
            "pass": "",
            "fail": '<failure message="boom">traceback</failure>',
            "skip": '<skipped message="no node"/>',
        }[outcome]
        bodies.append(
            f'<testcase classname="{classname}" name="{name}" time="0.01">'
            f"{inner}</testcase>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        f'<testsuites><testsuite name="pytest">{"".join(bodies)}'
        "</testsuite></testsuites>"
    )


def _module(text: str) -> str:
    """Return module text opening with the docstring a test module carries."""
    return f'# Copyright (c) The btclib developers\n"""{text}"""\n'


_LEDGER = """\
## The per-test ledger

| Core test | pin | read at | bitcoind | btclib-node |
| --- | --- | --- | --- | --- |
| `feature_blocksdir.py` | `0d1301b47a35` | 2026-03-24 | pass | skip (blk) |
| `p2p_invalid_messages.py` (wire) | `3fd68a95e68b` | 2026-04-07 | pass | pass |
| `p2p_invalid_messages.py` (inv, wire) | same | same | pass | fail |
| `feature_framework_miniwallet.py` \
| [`fa5f29774872`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) \
| 2025-12-16 | pass | skip |
| `rpc_getblockstats.py` | `b7cbd804284b` | 2026-05-25 | pass | skip (stats) |
"""

# the API's own full sha for a commit the ledger pins as `b7cbd804284b`
_STATS_SHA = "b7cbd804284bc0ffeec0ffeec0ffeec0ffee0123"

_STATS_CLASSNAME = "tests.integration.rpc_getblockstats_bitcoind_test"


def _stats_module(root: Path) -> None:
    """Write a module citing `rpc_getblockstats.py`, which loads a data file."""
    module = root / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "rpc_getblockstats_bitcoind_test.py").write_text(
        _module(
            "Read from Core's `test/functional/rpc_getblockstats.py`"
            " (`b7cbd804284b`, 2026-05-25)."
        ),
        encoding="utf-8",
    )


def test_ledger_pins_reads_the_first_row_and_resolves_same(
    verdict: ModuleType,
) -> None:
    """A `same` cell repeats the nearest explicit row above it."""
    pins = verdict.ledger_pins(_LEDGER)
    assert pins["feature_blocksdir.py"] == "0d1301b47a35"
    assert pins["p2p_invalid_messages.py"] == "3fd68a95e68b"


def test_ledger_pins_reads_a_markdown_link_cell(verdict: ModuleType) -> None:
    """A pin cell can carry a link around the backticked sha, not just one."""
    pins = verdict.ledger_pins(_LEDGER)
    assert pins["feature_framework_miniwallet.py"] == "fa5f29774872"


def test_ledger_pins_ignores_the_header_and_separator_rows(
    verdict: ModuleType,
) -> None:
    """`Core test` and `---` are not files, and name no pin."""
    pins = verdict.ledger_pins(_LEDGER)
    assert "Core test" not in pins
    assert "---" not in pins


def test_ledger_pins_ignores_a_row_naming_no_sha(verdict: ModuleType) -> None:
    """A row of another table, its second cell no commit, names no pin."""
    assert verdict.ledger_pins("| `not_a_row.py` | x | x | x | x |\n") == {}


def test_parse_junit_classifies_fail_skip_and_pass(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """A failure or an error is fail, a skip is skip, else pass."""
    report = tmp_path / "report.xml"
    report.write_text(
        _junit(
            [
                ("m", "test_a", "pass"),
                ("m", "test_b", "fail"),
                ("m", "test_c", "skip"),
            ]
        ),
        encoding="utf-8",
    )
    results = verdict.parse_junit(report)
    assert results == {"m::test_a": "pass", "m::test_b": "fail", "m::test_c": "skip"}


def test_parse_junit_reads_an_error_as_a_failure(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """An `<error>` element is a failure too, not a status of its own."""
    report = tmp_path / "report.xml"
    report.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="m" name="test_a" time="0.0">'
        '<error message="boom">traceback</error></testcase>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    assert verdict.parse_junit(report) == {"m::test_a": "fail"}


def test_master_only_failures_excludes_a_failure_shared_with_the_pin(
    verdict: ModuleType,
) -> None:
    """A test failing on both runs is not master-only."""
    master = {"m::a": "fail", "m::b": "fail", "m::c": "pass"}
    pinned = {"m::a": "fail", "m::b": "pass"}
    assert verdict.master_only_failures(master, pinned) == ["m::b"]


def test_master_only_failures_is_empty_where_nothing_disagrees(
    verdict: ModuleType,
) -> None:
    """No failure at all against master reports no master-only failure."""
    assert verdict.master_only_failures({"m::a": "pass"}, {"m::a": "pass"}) == []


def test_core_file_for_test_reads_the_module_own_citation(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """The Core file a test module names in its own first paragraph."""
    module = tmp_path / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "feature_blocksdir_bitcoind_test.py").write_text(
        _module(
            "Core's `feature_blocksdir`, rewritten on tf2's own harness.\n\n"
            "Read from Core's `test/functional/feature_blocksdir.py`"
            " (`0d1301b47a35`, 2026-03-24) rather than ported whole."
        ),
        encoding="utf-8",
    )
    core_file = verdict.core_file_for_test(
        "tests.integration.feature_blocksdir_bitcoind_test", tmp_path
    )
    assert core_file == "feature_blocksdir.py"


def test_core_file_for_test_tells_two_modules_of_one_file_apart(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """`p2p_invalid_messages` and its own `_misbehaving` module name one file.

    A module-name transform would have to special-case this pairing; this
    reads the citation each module states instead, which needs none.
    """
    module = tmp_path / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "p2p_invalid_messages_bitcoind_test.py").write_text(
        _module("Read from Core's `test/functional/p2p_invalid_messages.py`"),
        encoding="utf-8",
    )
    (module / "p2p_invalid_messages_misbehaving_bitcoind_test.py").write_text(
        _module(
            "Four more of its own checks.\n\n"
            "Read from Core's `test/functional/p2p_invalid_messages.py`"
        ),
        encoding="utf-8",
    )
    first = verdict.core_file_for_test(
        "tests.integration.p2p_invalid_messages_bitcoind_test", tmp_path
    )
    second = verdict.core_file_for_test(
        "tests.integration.p2p_invalid_messages_misbehaving_bitcoind_test", tmp_path
    )
    assert first == second == "p2p_invalid_messages.py"


def test_core_file_for_test_answers_none_for_a_missing_module(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """A module this repository has since removed names no Core file."""
    assert (
        verdict.core_file_for_test("tests.integration.gone_bitcoind_test", tmp_path)
        is None
    )


def test_core_file_for_test_answers_none_with_no_citation(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """A module carrying no citation sentence names no Core file either."""
    module = tmp_path / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "odd_bitcoind_test.py").write_text(
        _module("Nothing here mentions Core's own path at all."), encoding="utf-8"
    )
    assert (
        verdict.core_file_for_test("tests.integration.odd_bitcoind_test", tmp_path)
        is None
    )


def test_latest_commit_reads_the_sha_and_the_date(
    verdict: ModuleType, fake_gh: FakeGh
) -> None:
    """The commit `gh api` answers for the path, sha and date both."""
    fake_gh.commits[f"{_DIR}/feature_blocksdir.py"] = ("abc123", "2026-05-01")
    assert verdict._latest_commit(f"{_DIR}/feature_blocksdir.py") == (
        "abc123",
        "2026-05-01",
    )


def test_latest_commit_answers_none_for_a_path_never_upstream(
    verdict: ModuleType, fake_gh: FakeGh
) -> None:
    """No commit touching the path at all is a path `master` never held."""
    assert verdict._latest_commit(f"{_DIR}/gone.py") is None


# the API's own full sha for a commit the ledger pins as `0d1301b47a35`
_FULL_SHA = "0d1301b47a35c0ffeec0ffeec0ffeec0ffee0123"


_LATER_SHA = "fedcba987654" + "0" * 28


def test_has_moved_is_false_for_the_pin_itself_with_no_call(
    verdict: ModuleType, fake_gh: FakeGh
) -> None:
    """The pin itself has not moved, and needs no comparison to say so.

    The API answers a full sha, which the ledger's abbreviated pin is a
    prefix of ([ISS 83](https://github.com/btclib-org/functional-tests/issues/83)).
    """
    assert verdict.has_moved("0d1301b47a35", _FULL_SHA) is False
    assert fake_gh.calls == []


@pytest.mark.parametrize(
    "status,moved",
    [("behind", False), ("identical", False), ("ahead", True), ("diverged", True)],
)
def test_has_moved_reads_the_comparison_status(
    verdict: ModuleType, fake_gh: FakeGh, status: str, moved: bool
) -> None:
    """A commit in the pin's history has not moved; one ahead or aside has.

    A data file's last commit can be an ancestor of the pin rather than
    the pin itself, which the prefix alone would call moved.
    """
    fake_gh.statuses[f"0d1301b47a35...{_LATER_SHA}"] = status
    assert verdict.has_moved("0d1301b47a35", _LATER_SHA) is moved
    assert (
        fake_gh.calls[0][4]
        == f"repos/bitcoin/bitcoin/compare/0d1301b47a35...{_LATER_SHA}"
    )


def test_data_files_name_only_cited_core_files(verdict: ModuleType) -> None:
    """Each `_DATA_FILES` key is a Core file a `*_bitcoind_test.py` cites.

    A key no module cites any more is a mapping nothing reads; whether
    Core gained a data file is the census in the script's own docstring,
    which needs a Core checkout this suite does not have.
    """
    integration = Path(__file__).parent / "integration"
    cited = {
        match.group(1)
        for module in integration.glob("*_bitcoind_test.py")
        if (match := verdict._CITATION.search(module.read_text(encoding="utf-8")))
    }
    assert set(verdict._DATA_FILES) <= cited
    for data_files in verdict._DATA_FILES.values():
        assert data_files
        assert all(path.startswith("data/") for path in data_files)


def test_verdicts_classifies_a_master_only_failure(
    verdict: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """A failing test maps to its Core file, its pin, and a verdict."""
    module = tmp_path / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "feature_blocksdir_bitcoind_test.py").write_text(
        _module(
            "Read from Core's `test/functional/feature_blocksdir.py`"
            " (`0d1301b47a35`, 2026-03-24)."
        ),
        encoding="utf-8",
    )
    fake_gh.commits[f"{_DIR}/feature_blocksdir.py"] = ("fedcba987654", "2026-06-01")
    master = {"tests.integration.feature_blocksdir_bitcoind_test::test_a": "fail"}
    pinned = {"tests.integration.feature_blocksdir_bitcoind_test::test_a": "pass"}
    lines = verdict.verdicts(_LEDGER, master, pinned, tmp_path)
    assert len(lines) == 1
    assert "stale port" in lines[0]
    assert "feature_blocksdir.py" in lines[0]
    assert "check the port" not in lines[0]


def test_verdicts_sends_an_unchanged_file_to_the_port_first(
    verdict: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """An unchanged file names no cause, and the pinned file to read.

    A port that never matched its pinned file fails only against master
    too, so the line does not call it Core's regression
    ([ISS 143](https://github.com/btclib-org/functional-tests/issues/143)).
    """
    module = tmp_path / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "feature_blocksdir_bitcoind_test.py").write_text(
        _module(
            "Read from Core's `test/functional/feature_blocksdir.py`"
            " (`0d1301b47a35`, 2026-03-24)."
        ),
        encoding="utf-8",
    )
    fake_gh.commits[f"{_DIR}/feature_blocksdir.py"] = (_FULL_SHA, "2026-03-24")
    master = {"tests.integration.feature_blocksdir_bitcoind_test::test_a": "fail"}
    pinned = {"tests.integration.feature_blocksdir_bitcoind_test::test_a": "pass"}
    lines = verdict.verdicts(_LEDGER, master, pinned, tmp_path)
    assert lines == [
        (
            "- `tests.integration.feature_blocksdir_bitcoind_test::test_a`"
            " (`feature_blocksdir.py`): **file unchanged since the pin** --"
            " TF2.md pins `0d1301b47a35`, its last commit on master is"
            f" `{_FULL_SHA}` (2026-03-24): the cause is not measured, and the"
            " port may never have matched the file; check the port against"
            " `feature_blocksdir.py` at `0d1301b47a35` first"
        )
    ]
    assert "regression" not in lines[0]


def test_verdicts_names_a_data_file_that_moved_alone(
    verdict: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """A data file moved and its test file did not: a stale port, named.

    [ISS 146](https://github.com/btclib-org/functional-tests/issues/146).
    """
    _stats_module(tmp_path)
    fake_gh.commits[f"{_DIR}/rpc_getblockstats.py"] = (_STATS_SHA, "2026-05-25")
    fake_gh.commits[f"{_DIR}/data/rpc_getblockstats.json"] = (_LATER_SHA, "2026-07-01")
    master = {f"{_STATS_CLASSNAME}::test_a": "fail"}
    lines = verdict.verdicts(_LEDGER, master, {}, tmp_path)
    assert lines == [
        (
            f"- `{_STATS_CLASSNAME}::test_a` (`rpc_getblockstats.py`):"
            " **stale port** -- TF2.md pins `b7cbd804284b`, and"
            " `data/rpc_getblockstats.json` last changed on master in"
            f" `{_LATER_SHA}` (2026-07-01)"
        )
    ]


def test_verdicts_reads_an_older_data_file_as_unchanged(
    verdict: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """A data file whose last commit the pin's history holds has not moved."""
    _stats_module(tmp_path)
    fake_gh.commits[f"{_DIR}/rpc_getblockstats.py"] = (_STATS_SHA, "2026-05-25")
    fake_gh.commits[f"{_DIR}/data/rpc_getblockstats.json"] = (_LATER_SHA, "2019-05-10")
    fake_gh.statuses[f"b7cbd804284b...{_LATER_SHA}"] = "behind"
    master = {f"{_STATS_CLASSNAME}::test_a": "fail"}
    lines = verdict.verdicts(_LEDGER, master, {}, tmp_path)
    assert len(lines) == 1
    assert "**file unchanged since the pin**" in lines[0]
    assert (
        "and no commit since the pin touches `data/rpc_getblockstats.json`,"
        " which it loads:"
    ) in lines[0]


def test_verdicts_names_a_data_file_never_upstream(
    verdict: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """A data file with no commit upstream is named, not the test file."""
    _stats_module(tmp_path)
    fake_gh.commits[f"{_DIR}/rpc_getblockstats.py"] = (_STATS_SHA, "2026-05-25")
    master = {f"{_STATS_CLASSNAME}::test_a": "fail"}
    lines = verdict.verdicts(_LEDGER, master, {}, tmp_path)
    assert len(lines) == 1
    assert "no commit touching `data/rpc_getblockstats.json` at all" in lines[0]


def test_verdicts_names_a_test_with_no_core_citation(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """No module on disk for the failing classname: named, not raised."""
    master = {"tests.integration.gone_bitcoind_test::test_a": "fail"}
    lines = verdict.verdicts(_LEDGER, master, {}, tmp_path)
    assert len(lines) == 1
    assert "no Core file citation" in lines[0]


def test_verdicts_names_a_file_the_ledger_has_no_row_for(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """A Core file this ledger fixture never pinned is named, not guessed at."""
    module = tmp_path / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "rpc_uptime_bitcoind_test.py").write_text(
        _module("Read from Core's `test/functional/rpc_uptime.py` (`x`, 2026-06-13)."),
        encoding="utf-8",
    )
    master = {"tests.integration.rpc_uptime_bitcoind_test::test_a": "fail"}
    lines = verdict.verdicts(_LEDGER, master, {}, tmp_path)
    assert len(lines) == 1
    assert "names no row" in lines[0]


def test_verdicts_names_a_path_never_upstream(
    verdict: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """A ledger pin whose path has no commit upstream is named as such."""
    module = tmp_path / "tests" / "integration"
    module.mkdir(parents=True)
    (module / "feature_blocksdir_bitcoind_test.py").write_text(
        _module(
            "Read from Core's `test/functional/feature_blocksdir.py`"
            " (`0d1301b47a35`, 2026-03-24)."
        ),
        encoding="utf-8",
    )
    master = {"tests.integration.feature_blocksdir_bitcoind_test::test_a": "fail"}
    lines = verdict.verdicts(_LEDGER, master, {}, tmp_path)
    assert len(lines) == 1
    assert "no commit touching `feature_blocksdir.py` at all" in lines[0]


def test_verdicts_is_empty_with_no_master_only_failure(
    verdict: ModuleType, tmp_path: Path
) -> None:
    """Nothing failing only against master reports nothing at all."""
    assert verdict.verdicts(_LEDGER, {"m::a": "pass"}, {"m::a": "pass"}, tmp_path) == []


def test_main_usage_error(verdict: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fewer or more than three arguments is a usage error, exit 2."""
    monkeypatch.setattr(sys, "argv", ["prog", "one", "two"])
    assert verdict.main() == 2


def test_main_prints_agreement_with_no_master_only_failure(
    verdict: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """No disagreement prints agreement and nothing else."""
    ledger = tmp_path / "TF2.md"
    ledger.write_text(_LEDGER, encoding="utf-8")
    master = tmp_path / "master.xml"
    master.write_text(_junit([("m", "test_a", "pass")]), encoding="utf-8")
    pinned = tmp_path / "pinned.xml"
    pinned.write_text(_junit([("m", "test_a", "pass")]), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), str(master), str(pinned)])
    monkeypatch.chdir(tmp_path)
    assert verdict.main() == 0
    out = capsys.readouterr().out
    assert "No test fails" in out


def test_main_prints_a_master_only_verdict(
    verdict: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A master-only failure prints its own report line."""
    ledger = tmp_path / "TF2.md"
    ledger.write_text(_LEDGER, encoding="utf-8")
    classname = "tests.integration.feature_blocksdir_bitcoind_test"
    module_dir = tmp_path / "tests" / "integration"
    module_dir.mkdir(parents=True)
    (module_dir / "feature_blocksdir_bitcoind_test.py").write_text(
        _module(
            "Read from Core's `test/functional/feature_blocksdir.py`"
            " (`0d1301b47a35`, 2026-03-24)."
        ),
        encoding="utf-8",
    )
    fake_gh.commits[f"{_DIR}/feature_blocksdir.py"] = (_FULL_SHA, "2026-03-24")
    master = tmp_path / "master.xml"
    master.write_text(_junit([(classname, "test_a", "fail")]), encoding="utf-8")
    pinned = tmp_path / "pinned.xml"
    pinned.write_text(_junit([(classname, "test_a", "pass")]), encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), str(master), str(pinned)])
    monkeypatch.chdir(tmp_path)
    assert verdict.main() == 0
    out = capsys.readouterr().out
    assert "file unchanged since the pin" in out
    assert "feature_blocksdir.py" in out


def _failing_gh(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    stderrs: list[str],
    stdout: str,
) -> tuple[list[list[str]], list[int]]:
    """Make `gh` fail with each of `stderrs` in turn, then answer `stdout`.

    Returns the argv of every call and the seconds slept between them.
    """
    calls: list[list[str]] = []
    slept: list[int] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if len(calls) <= len(stderrs):
            raise subprocess.CalledProcessError(1, argv, stderr=stderrs[len(calls) - 1])
        return subprocess.CompletedProcess(argv, 0, stdout=stdout)

    monkeypatch.setattr(verdict.subprocess, "run", run)
    monkeypatch.setattr(verdict.time, "sleep", slept.append)
    return calls, slept


# each gh call whose output the script reads: the call, what gh answers,
# what it returns
_CALLS: dict[str, tuple[Callable[[ModuleType], object], str, object]] = {
    "commits": (lambda m: m._latest_commit("p"), "[]", None),
    "compare": (lambda m: m._in_pin_history("abc", "def"), "ahead\n", False),
}


@pytest.mark.parametrize("name", list(_CALLS))
@pytest.mark.parametrize(
    "stderr",
    [
        "gh: Not Found (HTTP 404)\n",
        "gh: API rate limit exceeded for user ID 1. (HTTP 403)\n",
        "gh: You have exceeded a secondary rate limit (HTTP 403)\n",
    ],
)
def test_a_failed_gh_call_prints_its_stderr_and_is_not_retried(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    stderr: str,
) -> None:
    """Each call through `_gh_output` shows gh's words, once, and gives up.

    [ISS 457](https://github.com/btclib-org/functional-tests/issues/457).
    """
    call, stdout, _ = _CALLS[name]
    calls, slept = _failing_gh(verdict, monkeypatch, [stderr], stdout)

    with pytest.raises(subprocess.CalledProcessError):
        call(verdict)

    assert stderr.strip() in capsys.readouterr().err
    assert len(calls) == 1
    assert slept == []


@pytest.mark.parametrize("name", list(_CALLS))
def test_a_server_error_is_retried_and_the_retry_answers(
    verdict: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    """A 5xx is waited out in each `_gh_output` call, and each failure shown."""
    call, stdout, expected = _CALLS[name]
    stderr = "gh: Bad Gateway (HTTP 502)\n"
    calls, slept = _failing_gh(verdict, monkeypatch, [stderr, stderr], stdout)

    assert call(verdict) == expected

    assert len(calls) == 3
    assert slept == [5, 10]
    assert capsys.readouterr().err.count(stderr.strip()) == 2


def test_a_server_error_that_persists_is_raised_after_the_last_attempt(
    verdict: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three attempts in all, and then the error is the caller's."""
    call, stdout, _ = next(iter(_CALLS.values()))
    calls, _ = _failing_gh(
        verdict, monkeypatch, ["gh: Bad Gateway (HTTP 502)\n"] * 4, stdout
    )

    with pytest.raises(subprocess.CalledProcessError):
        call(verdict)

    assert len(calls) == 3


def test_the_script_exits_with_main_s_own_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run as the workflow step runs it, the exit status is `main`'s."""
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert excinfo.value.code == 2
