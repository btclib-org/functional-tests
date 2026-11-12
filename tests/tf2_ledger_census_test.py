# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Tests for the census and per-test pin reads of `.github/scripts`.

Its only external dependency is `gh`, called for the default branch tip,
for the recursive tree listing, for a path's last commit, for a
comparison of two commits, and for the tracking issues. Every test that
reaches it replaces `subprocess.run`: with `FakeGh`, which answers each
call the way a real `gh api`/`gh issue` would rather than reaching
GitHub, or with `_failing_gh`'s stand-in, which fails as gh does. The
answers for a path's last commit and for a comparison are recorded from
the API, cut to the fields the script reads.

The script is loaded by path, `.github/scripts` being no package, the
same idiom `tf2_ledger_test.py` uses for `check_vendored_vectors.py`.
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

_SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "tf2_ledger_census.py"


@pytest.fixture
def census(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return the script, imported by path, registered before it runs."""
    spec = importlib.util.spec_from_file_location("tf2_ledger_census", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "tf2_ledger_census", module)
    spec.loader.exec_module(module)
    return module


class FakeGh:
    """A `subprocess.run` stand-in, answering by which `gh` call this is.

    `tip` is the sha `_default_branch_tip` should read back. `files` is
    the tree entries the recursive listing answers with, as (path, type)
    pairs; `truncated` toggles the flag the listing carries. `commits`
    answers a path's last commit, by path, with `[]` for a path not in
    it; `compare` answers a comparison's `status`, by `base...head`.
    `issues` is the open issues the search answers with, as (number,
    title) pairs. Every call is recorded in `calls`.
    """

    def __init__(self) -> None:
        self.tip = "0" * 40
        self.files: list[tuple[str, str]] = []
        self.truncated = False
        self.commits: dict[str, str] = {}
        self.compare: dict[str, str] = {}
        self.issues: list[tuple[int, str]] = []
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Record the call, and answer as the `gh` sub-command it names."""
        self.calls.append(list(argv))
        if argv[1] == "api" and any(a.endswith("/commits/HEAD") for a in argv):
            return subprocess.CompletedProcess(argv, 0, stdout=self.tip)
        if argv[1] == "api" and any(a.endswith("/commits") for a in argv):
            [path] = [a.removeprefix("path=") for a in argv if a.startswith("path=")]
            return subprocess.CompletedProcess(
                argv, 0, stdout=self.commits.get(path, "[]")
            )
        if argv[1] == "api" and any("/compare/" in a for a in argv):
            [spec] = [a.split("/compare/")[1] for a in argv if "/compare/" in a]
            return subprocess.CompletedProcess(argv, 0, stdout=self.compare[spec])
        if argv[1] == "api" and any("/git/trees/" in a for a in argv):
            tree = {
                "truncated": self.truncated,
                "tree": [{"path": path, "type": kind} for path, kind in self.files],
            }
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(tree))
        if argv[1] == "issue" and argv[2] == "list":
            issues = [{"number": n, "title": t} for n, t in self.issues]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(issues))
        return subprocess.CompletedProcess(argv, 0, stdout="")


@pytest.fixture
def fake_gh(census: ModuleType, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    """Install a `FakeGh` in place of the script's own `subprocess.run`."""
    fake = FakeGh()
    monkeypatch.setattr(census.subprocess, "run", fake)
    return fake


_DIR = "test/functional/test_framework"


def test_upstream_python_files_lists_recursively(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """Every `.py` blob under the directory, at any depth, and nothing else."""
    fake_gh.files = [
        (f"{_DIR}", "tree"),
        (f"{_DIR}/address.py", "blob"),
        (f"{_DIR}/crypto", "tree"),
        (f"{_DIR}/crypto/hkdf.py", "blob"),
        (f"{_DIR}/bip340_test_vectors.csv", "blob"),
        ("test/functional/other_dir/x.py", "blob"),
    ]
    found = census.upstream_python_files("bitcoin/bitcoin", _DIR, "abc")
    assert found == ["address.py", "crypto/hkdf.py"]


def test_upstream_python_files_refuses_a_truncated_listing(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """`.truncated: true` is checked rather than assumed false."""
    fake_gh.truncated = True
    with pytest.raises(RuntimeError, match="truncated"):
        census.upstream_python_files("bitcoin/bitcoin", _DIR, "abc")


def test_upstream_python_files_forces_a_get(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """`-f recursive=1` alone makes `gh api` default to POST, which 404s.

    Measured against the real endpoint: `gh api
    repos/<owner>/<repo>/git/trees/<sha> -f recursive=1` answers "Not
    Found" because `-f` switches `gh api`'s own default method to POST,
    and this endpoint takes none; `--method GET` ahead of it is what
    keeps the call a read. `FakeGh` answers either way, so nothing but
    this assertion on the recorded call would have caught it -- it is
    what a real dispatch of `vendored-vectors.yml` did catch, `FakeGh`
    answering the wrong method just as readily as the right one.
    """
    census.upstream_python_files("bitcoin/bitcoin", _DIR, "abc")
    [call] = [c for c in fake_gh.calls if any("/git/trees/" in a for a in c)]
    assert "--method" in call
    assert call[call.index("--method") + 1] == "GET"


def test_ledger_entries_reads_the_headings(census: ModuleType) -> None:
    r"""The upstream-relative path of every `### \`...\`` heading."""
    text = f"### `{_DIR}/address.py`\n\ntext\n\n### `{_DIR}/crypto/hkdf.py`\n\ntext\n"
    assert census.ledger_entries(text) == {"address.py", "crypto/hkdf.py"}


def test_report_opens_an_issue_on_a_disagreement(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """A gained or a removed path opens an issue naming it."""
    census.report("title", "a" * 40, ["signet.py"], ["gone.py"])
    create = next(call for call in fake_gh.calls if call[1:3] == ["issue", "create"])
    body = create[create.index("--body") + 1]
    assert "signet.py" in body
    assert "gone.py" in body


def test_report_lists_a_removed_path_alone(census: ModuleType, fake_gh: FakeGh) -> None:
    """A removed path with nothing gained opens an issue with no gained list."""
    census.report("title", "a" * 40, [], ["gone.py"])
    create = next(call for call in fake_gh.calls if call[1:3] == ["issue", "create"])
    body = create[create.index("--body") + 1]
    assert "gone.py" in body
    assert "Gained" not in body


def test_report_edits_an_open_issue_instead_of_creating_a_second(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """A second disagreement updates the issue the first one opened."""
    fake_gh.issues = [(42, "title")]
    census.report("title", "a" * 40, ["signet.py"], [])
    edit = next(call for call in fake_gh.calls if call[1:3] == ["issue", "edit"])
    assert edit[3] == "42"


def test_report_closes_an_open_issue_once_the_census_agrees(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """No disagreement closes whatever issue was tracking one."""
    fake_gh.issues = [(42, "title")]
    census.report("title", "a" * 40, [], [])
    close = next(call for call in fake_gh.calls if call[1:3] == ["issue", "close"])
    assert close[3] == "42"


def test_report_does_nothing_where_nothing_was_open(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """No disagreement and no open issue: nothing to close either."""
    census.report("title", "a" * 40, [], [])
    assert all(call[1:3] != ["issue", "close"] for call in fake_gh.calls)


_PER_TEST = "## The per-test ledger"
_HEADER = "| Core test | pin | read at | bitcoind | btclib-node |\n| --- | --- | --- | --- | --- |"


def _ledger(tmp_path: Path, headings: list[str], rows: list[str]) -> Path:
    """Write a ledger: a framework entry per heading, then a per-test table."""
    entries = "".join(f"### `{_DIR}/{path}`\n\ntext\n\n" for path in headings)
    ledger = tmp_path / "TF2.md"
    ledger.write_text(
        f"{entries}{_PER_TEST}\n\n{_HEADER}\n" + "".join(f"{r}\n" for r in rows),
        encoding="utf-8",
    )
    return ledger


def _argv(ledger: Path, *, dry_run: bool) -> list[str]:
    return ["prog", str(ledger), "a title", "pins title"] + (
        ["--dry-run"] if dry_run else []
    )


def test_main_end_to_end(
    census: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`main` reads argv, compares, prints, and reports unless `--dry-run`."""
    ledger = _ledger(
        tmp_path,
        ["address.py"],
        ["| `rpc_whitelist.py` | `fa24693819e0` | x | y | z |"],
    )
    fake_gh.files = [
        (f"{_DIR}/address.py", "blob"),
        (f"{_DIR}/signet.py", "blob"),
    ]
    fake_gh.commits = {"test/functional/rpc_whitelist.py": _WHITELIST_COMMITS}
    fake_gh.compare = {
        "fa24693819e0...fad441867c9baa8c4ae9a755f09948efa1b23447": "ahead"
    }

    monkeypatch.setattr(sys, "argv", _argv(ledger, dry_run=True))
    assert census.main() == 0
    out = capsys.readouterr().out
    assert "GAINED: signet.py" in out
    assert "BEHIND: `rpc_whitelist.py`: `fa24693819e0` -> `fad441867c9b`" in out
    assert all(call[1] != "issue" for call in fake_gh.calls)

    monkeypatch.setattr(sys, "argv", _argv(ledger, dry_run=False))
    assert census.main() == 0
    created = [c for c in fake_gh.calls if c[1:3] == ["issue", "create"]]
    assert [c[c.index("--title") + 1] for c in created] == ["a title", "pins title"]


def test_main_reports_a_removed_path(
    census: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An entry upstream no longer has prints REMOVED, not GAINED."""
    ledger = _ledger(
        tmp_path,
        ["address.py", "gone.py"],
        ["| `feature_filelock.py` | `fa5f29774872` | x | y | z |"],
    )
    fake_gh.files = [(f"{_DIR}/address.py", "blob")]
    fake_gh.commits = {"test/functional/feature_filelock.py": _FILELOCK_COMMITS}

    monkeypatch.setattr(sys, "argv", _argv(ledger, dry_run=True))
    assert census.main() == 0
    out = capsys.readouterr().out
    assert "REMOVED: gone.py" in out
    assert "GAINED" not in out


def test_main_reports_no_disagreement(
    census: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A ledger that matches upstream prints agreement and nothing else."""
    ledger = _ledger(
        tmp_path,
        ["address.py"],
        ["| `feature_filelock.py` | `fa5f29774872` | x | y | z |"],
    )
    fake_gh.files = [(f"{_DIR}/address.py", "blob")]
    fake_gh.commits = {"test/functional/feature_filelock.py": _FILELOCK_COMMITS}

    monkeypatch.setattr(sys, "argv", _argv(ledger, dry_run=True))
    assert census.main() == 0
    out = capsys.readouterr().out
    assert "agrees with upstream" in out
    assert "Every per-test pin in TF2.md is at its tip or held" in out
    assert "GAINED" not in out
    assert "REMOVED" not in out
    assert "BEHIND" not in out


def test_main_prints_a_held_pin_and_a_stale_marker(
    census: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A held pin prints HELD where it is behind, HELD AT TIP where not."""
    ledger = _ledger(
        tmp_path,
        [],
        [
            "| `rpc_whitelist.py` | `fa24693819e0` held | x | y | z |",
            "| `feature_filelock.py` | `fa5f29774872` held | x | y | z |",
        ],
    )
    fake_gh.commits = {
        "test/functional/rpc_whitelist.py": _WHITELIST_COMMITS,
        "test/functional/feature_filelock.py": _FILELOCK_COMMITS,
    }
    fake_gh.compare = {
        "fa24693819e0...fad441867c9baa8c4ae9a755f09948efa1b23447": "ahead"
    }

    monkeypatch.setattr(sys, "argv", _argv(ledger, dry_run=False))
    assert census.main() == 0
    out = capsys.readouterr().out
    assert "HELD: `rpc_whitelist.py`" in out
    assert "HELD AT TIP: feature_filelock.py" in out
    [create] = [
        c for c in fake_gh.calls if c[1:3] == ["issue", "create"] and "pins title" in c
    ]
    body = create[create.index("--body") + 1]
    assert "feature_filelock.py" in body
    assert "rpc_whitelist.py" not in body


def test_main_usage_error(census: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Too few or too many arguments, or one title twice, is a usage error."""
    monkeypatch.setattr(sys, "argv", ["prog", "one-arg"])
    assert census.main() == 2
    monkeypatch.setattr(sys, "argv", ["prog", "ledger", "a title"])
    assert census.main() == 2
    monkeypatch.setattr(sys, "argv", ["prog", "ledger", "a title", "a title"])
    assert census.main() == 2


# `gh api --method GET repos/bitcoin/bitcoin/commits -f path=<path> -f
# sha=b048a3e8386a -f per_page=1`, recorded on 2026-10-30 and cut to the
# fields `_latest_commit` reads
_WHITELIST_COMMITS = json.dumps(
    [
        {
            "sha": "fad441867c9baa8c4ae9a755f09948efa1b23447",
            "commit": {"committer": {"date": "2026-10-12T07:42:24Z"}},
        }
    ]
)
_FILELOCK_COMMITS = json.dumps(
    [
        {
            "sha": "fa5f29774872d18febc0df38831a6e45f3de69cc",
            "commit": {"committer": {"date": "2025-12-16T21:21:15Z"}},
        }
    ]
)
_FULL_WHITELIST = "fad441867c9baa8c4ae9a755f09948efa1b23447"


@pytest.mark.parametrize(
    "cell, expected",
    [
        ("`fa24693819e0`", ("fa24693819e0", False)),
        (
            "[`fa24693819e0`](https://github.com/bitcoin/bitcoin/commit/fa24693819e0)",
            ("fa24693819e0", False),
        ),
        ("`fa24693819e0` held", ("fa24693819e0", True)),
        (
            "[`fa24693819e0`](https://github.com/bitcoin/bitcoin/commit/fa24693819e0) held",
            ("fa24693819e0", True),
        ),
    ],
)
def test_per_test_pins_reads_each_pin_cell(
    census: ModuleType, cell: str, expected: tuple[str, bool]
) -> None:
    """A pin bare or linked, with or without `held`; `same` repeats it."""
    text = (
        f"{_PER_TEST}\n\n{_HEADER}\n| `rpc_users.py` | {cell} | x | y | z |\n"
        "| `rpc_users.py` (`-norpcauth`) | same | same | y | z |\n"
        "\n## Next\n\n| `later.py` | nothing a pin cell reads | x | y | z |\n"
    )
    assert census.per_test_pins(text) == {"rpc_users.py": census.Pin(*expected)}


def test_per_test_pins_reads_tf2_md(census: ModuleType) -> None:
    """Every row of the tree's own table is in a shape the script reads."""
    text = (Path(__file__).parents[1] / "TF2.md").read_text(encoding="utf-8")
    assert census.per_test_pins(text)


@pytest.mark.parametrize(
    "rows, match",
    [
        (["| `x.py` | `nothex` | x | y | z |"], "no pin reads"),
        (
            [
                "| `x.py` | [`fa24693819e0`](https://github.com/bitcoin/bitcoin/commit/fa5f29774872) | x | y | z |"
            ],
            "no pin reads",
        ),
        (["| `x.py` | `fa24693819e0` kept | x | y | z |"], "no pin reads"),
        (["| `x.py` | same | same | y | z |"], "with no row above"),
        (["| `noext` | `fa24693819e0` | x | y | z |"], "no Core test file"),
        (["| x.py | `fa24693819e0` | x | y | z |"], "no Core test file"),
        (["|`x.py`|`fa24693819e0`|x|y|z|", "| x.py | same | x | y | z |"], "no Core"),
        (["| `x.py` | `fa24693819e0` | x | y |"], "a row of 4 cells, not five"),
        (["| `x.py` | `FA24693819E0` | x | y | z |"], "no pin reads"),
        (["| `x.py` | `fa24693819e0` **held** | x | y | z |"], "no pin reads"),
        ([], "no row under"),
        (
            [
                "| `x.py` | `fa24693819e0` | x | y | z |",
                "| `x.py` (log) | `fa24693819e0` held | x | y | z |",
            ],
            "where a row above pins",
        ),
    ],
)
def test_per_test_pins_refuses_a_row_it_cannot_read(
    census: ModuleType, rows: list[str], match: str
) -> None:
    """A row in no shape the table defines stops the run, never skipped."""
    text = f"{_PER_TEST}\n\n{_HEADER}\n" + "".join(f"{r}\n" for r in rows)
    with pytest.raises(ValueError, match=match):
        census.per_test_pins(text)


def test_per_test_pins_reads_an_indented_row(census: ModuleType) -> None:
    """A row indented in the source is read, not skipped."""
    text = f"{_PER_TEST}\n\n{_HEADER}\n | `x.py` | `fa24693819e0` | x | y | z |\n"
    assert census.per_test_pins(text) == {"x.py": census.Pin("fa24693819e0", False)}


def test_main_reports_a_held_pin_whose_path_no_commit_touches(
    census: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held pin is still listed where upstream has no commit for its path."""
    ledger = _ledger(
        tmp_path, [], ["| `gone_test.py` | `fa24693819e0` held | x | y | z |"]
    )
    monkeypatch.setattr(sys, "argv", _argv(ledger, dry_run=False))
    assert census.main() == 0
    [create] = [
        c for c in fake_gh.calls if c[1:3] == ["issue", "create"] and "pins title" in c
    ]
    body = create[create.index("--body") + 1]
    assert "- `gone_test.py`: `fa24693819e0` -> no commit" in body


def test_per_test_pins_refuses_a_ledger_without_the_section(
    census: ModuleType,
) -> None:
    """No per-test section is refused, not read as no pins."""
    with pytest.raises(ValueError, match="no '## The per-test ledger' section"):
        census.per_test_pins(f"### `{_DIR}/address.py`\n")


def test_pin_drift_asks_only_for_a_tip_the_pin_does_not_name(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """A tip the pin abbreviates is at the tip, with no comparison made."""
    fake_gh.commits = {"test/functional/feature_filelock.py": _FILELOCK_COMMITS}
    pins = {"feature_filelock.py": census.Pin("fa5f29774872", held=False)}
    assert census.pin_drift("bitcoin/bitcoin", pins, "abc") == []
    assert not any(any("/compare/" in a for a in c) for c in fake_gh.calls)
    [call] = [
        c for c in fake_gh.calls if "path=test/functional/feature_filelock.py" in c
    ]
    assert "sha=abc" in call
    assert call[call.index("--method") + 1] == "GET"


@pytest.mark.parametrize("status, drifted", [("ahead", True), ("behind", False)])
def test_pin_drift_compares_a_tip_the_pin_does_not_name(
    census: ModuleType, fake_gh: FakeGh, status: str, drifted: bool
) -> None:
    """A tip ahead of the pin is drift; one in the pin's history is not.

    `ahead` is `compare/fa24693819e0...fad441867c9b`, `behind` the same
    two commits the other way round, both recorded on 2026-10-30.
    """
    fake_gh.commits = {"test/functional/rpc_whitelist.py": _WHITELIST_COMMITS}
    fake_gh.compare = {f"fa24693819e0...{_FULL_WHITELIST}": status}
    pin = census.Pin("fa24693819e0", held=False)
    drift = census.pin_drift("bitcoin/bitcoin", {"rpc_whitelist.py": pin}, "abc")
    expected = [census.Drift("rpc_whitelist.py", pin, (_FULL_WHITELIST, "2026-10-12"))]
    assert drift == (expected if drifted else [])


def test_pin_drift_reports_a_path_no_commit_touches(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """A path upstream has no commit for is drift, its tip None."""
    pin = census.Pin("fa24693819e0", held=False)
    [drift] = census.pin_drift("bitcoin/bitcoin", {"no_such_test.py": pin}, "abc")
    assert drift.tip is None
    assert drift.line() == "`no_such_test.py`: `fa24693819e0` -> no commit"


def test_report_pins_lists_each_pin_behind_and_each_stale_marker(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """The issue names each pin behind with its tip, and each stale marker."""
    drift = census.Drift(
        "rpc_whitelist.py",
        census.Pin("fa24693819e0", held=False),
        (_FULL_WHITELIST, "2026-10-12"),
    )
    census.report_pins("pins title", "b" * 40, [drift], ["feature_filelock.py"])
    create = next(call for call in fake_gh.calls if call[1:3] == ["issue", "create"])
    body = create[create.index("--body") + 1]
    assert "- `rpc_whitelist.py`: `fa24693819e0` -> `fad441867c9b` (2026-10-12)" in body
    assert "- `feature_filelock.py`" in body


def test_report_pins_closes_its_issue_once_every_pin_is_at_its_tip(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """Nothing behind and no stale marker closes the pins' issue."""
    fake_gh.issues = [(7, "pins title")]
    census.report_pins("pins title", "b" * 40, [], [])
    close = next(call for call in fake_gh.calls if call[1:3] == ["issue", "close"])
    assert close[3] == "7"


def test_open_issue_number_takes_only_an_exact_title(
    census: ModuleType, fake_gh: FakeGh
) -> None:
    """The search matches words, so a title that only contains them is not it.

    `"per-test pins" in:title` answers "Twelve more of TF2.md's per-test
    pins are behind upstream", measured on 2026-10-30.
    """
    fake_gh.issues = [
        (538, "Twelve more of TF2.md's per-test pins are behind upstream")
    ]
    assert census._open_issue_number("per-test pins") is None
    fake_gh.issues.append((9, "per-test pins"))
    assert census._open_issue_number("per-test pins") == "9"


def _failing_gh(
    census: ModuleType,
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

    monkeypatch.setattr(census.subprocess, "run", run)
    monkeypatch.setattr(census.time, "sleep", slept.append)
    return calls, slept


# each gh call whose output the script reads: the call, what gh answers,
# what it returns
_CALLS: dict[str, tuple[Callable[[ModuleType], object], str, object]] = {
    "tip": (lambda m: m._default_branch_tip("r"), "abc\n", "abc"),
    "trees": (
        lambda m: m.upstream_python_files("r", "d", "abc"),
        '{"tree": []}',
        [],
    ),
    "issue-list": (lambda m: m._open_issue_number("A title"), "[]", None),
    "commits": (lambda m: m._latest_commit("r", "p", "abc"), "[]", None),
    "compare": (lambda m: m._in_pin_history("r", "a", "b"), "identical\n", True),
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
    census: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
    stderr: str,
) -> None:
    """Each call through `_gh_output` shows gh's words, once, and gives up.

    [ISS 457](https://github.com/btclib-org/functional-tests/issues/457).
    """
    call, stdout, _ = _CALLS[name]
    calls, slept = _failing_gh(census, monkeypatch, [stderr], stdout)

    with pytest.raises(subprocess.CalledProcessError):
        call(census)

    assert stderr.strip() in capsys.readouterr().err
    assert len(calls) == 1
    assert slept == []


@pytest.mark.parametrize("name", list(_CALLS))
def test_a_server_error_is_retried_and_the_retry_answers(
    census: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    name: str,
) -> None:
    """A 5xx is waited out in each `_gh_output` call, and each failure shown."""
    call, stdout, expected = _CALLS[name]
    stderr = "gh: Bad Gateway (HTTP 502)\n"
    calls, slept = _failing_gh(census, monkeypatch, [stderr, stderr], stdout)

    assert call(census) == expected

    assert len(calls) == 3
    assert slept == [5, 10]
    assert capsys.readouterr().err.count(stderr.strip()) == 2


def test_a_server_error_that_persists_is_raised_after_the_last_attempt(
    census: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three attempts in all, and then the error is the caller's."""
    call, stdout, _ = next(iter(_CALLS.values()))
    calls, _ = _failing_gh(
        census, monkeypatch, ["gh: Bad Gateway (HTTP 502)\n"] * 4, stdout
    )

    with pytest.raises(subprocess.CalledProcessError):
        call(census)

    assert len(calls) == 3


def test_the_script_exits_with_main_s_own_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run as the workflow step runs it, the exit status is `main`'s."""
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert excinfo.value.code == 2
