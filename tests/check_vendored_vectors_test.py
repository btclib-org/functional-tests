# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Tests for the weekly pin re-check of `.github/scripts`.

Its external dependency is `gh`. Every test that reaches it replaces
`subprocess.run`: with `FakeGh`, which answers the way a real
`gh api repos/<repo>/commits` would rather than reaching GitHub -- with
the commit that last touched a path, the one that deleted or renamed
it included, and with an empty list for a path the branch walked never
held -- or with `_failing_gh`'s stand-in, which fails as gh does.

The script is loaded by path, `.github/scripts` being no package, the
same idiom `tf2_ledger_test.py` uses for it.
"""

from __future__ import annotations

import importlib.util
import json
import runpy
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import Mock

import pytest

_SCRIPT = (
    Path(__file__).parents[1] / ".github" / "scripts" / "check_vendored_vectors.py"
)

_PATH = "test/functional/test_framework/bignum.py"

_PIN = "0d1301b47a35"

# the API's own full sha for the commit that deleted the pinned file
_REMOVING_SHA = "3ed772d221" + "0" * 30

_LEDGER = f"""\
### `bignum.py`

```text
repo    bitcoin/bitcoin
path    {_PATH}
commit  {_PIN}
behind  0
```
"""


@pytest.fixture
def checker(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return the script, imported by path, registered before it runs."""
    spec = importlib.util.spec_from_file_location("check_vendored_vectors", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_vendored_vectors", module)
    spec.loader.exec_module(module)
    return module


class FakeGh:
    """A `subprocess.run` stand-in for `gh api .../commits` and `gh issue`.

    `commits` maps a path to the (sha, date) the listing names first; a
    path absent from it answers with an empty list. `issues` holds the
    numbers of the open issues `gh issue list` answers with. Every call
    is recorded in `calls`.
    """

    def __init__(self) -> None:
        self.commits: dict[str, tuple[str, str]] = {}
        self.issues: list[int] = []
        self.calls: list[list[str]] = []

    def __call__(
        self, argv: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        """Record the call, and answer the path or the issues it asked about."""
        self.calls.append(list(argv))
        if argv[1] == "issue":
            issues = [{"number": number} for number in self.issues]
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(issues))
        path = next(a.removeprefix("path=") for a in argv if a.startswith("path="))
        found = self.commits.get(path)
        commits = (
            [
                {
                    "sha": found[0],
                    "commit": {"committer": {"date": f"{found[1]}T00:00:00Z"}},
                }
            ]
            if found
            else []
        )
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(commits))


@pytest.fixture
def fake_gh(checker: ModuleType, monkeypatch: pytest.MonkeyPatch) -> FakeGh:
    """Install a `FakeGh` in place of the script's `subprocess.run`."""
    fake = FakeGh()
    monkeypatch.setattr(checker.subprocess, "run", fake)
    return fake


def test_latest_commit_answers_none_for_a_path_never_held(
    checker: ModuleType, fake_gh: FakeGh
) -> None:
    """An empty listing is a path the branch walked never held."""
    assert checker._latest_commit("bitcoin/bitcoin", _PATH) is None


def test_latest_commit_passes_a_ref_as_the_sha_parameter(
    checker: ModuleType, fake_gh: FakeGh
) -> None:
    """A `ref` is the branch the listing walks."""
    checker._latest_commit("bitcoin/bitcoin", _PATH, "some-branch")
    assert "sha=some-branch" in fake_gh.calls[0]


def test_a_removing_commit_is_drift_that_may_be_a_removal(
    checker: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """A deleted pin's tip is the deleting commit, named as possibly that.

    [ISS 150](https://github.com/btclib-org/functional-tests/issues/150).
    """
    ledger = tmp_path / "TF2.md"
    ledger.write_text(_LEDGER, encoding="utf-8")
    fake_gh.commits[_PATH] = (_REMOVING_SHA, "2019-10-30")
    drifted, skipped = checker.find_drift(ledger)
    assert skipped == []
    assert [d.latest_commit for d in drifted] == [_REMOVING_SHA]
    assert not drifted[0].has_no_tip
    body = checker._issue_body(ledger, drifted, skipped)
    assert (
        f"the latest commit touching `{_PATH}` is now `{_REMOVING_SHA}`"
        " (2019-10-30), `bitcoin/bitcoin` -- which may have deleted or renamed"
        " the file rather than changed it"
    ) in body


def test_an_empty_listing_names_the_branch_walked(
    checker: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """No commit at all is named as a path that branch never held."""
    ledger = tmp_path / "TF2.md"
    ledger.write_text(
        _LEDGER.replace("behind", "ref     pr-branch\nbehind"), encoding="utf-8"
    )
    drifted, skipped = checker.find_drift(ledger)
    assert [d.has_no_tip for d in drifted] == [True]
    body = checker._issue_body(ledger, drifted, skipped)
    assert (
        "no commit on `pr-branch` of `bitcoin/bitcoin` touches"
        f" `{_PATH}` -- a path that branch never held"
    ) in body
    unnamed = checker.Drift(checker.Entry("h", "bitcoin/bitcoin", _PATH, _PIN), "", "")
    assert "no commit on the default branch of" in checker._issue_body(
        ledger, [unnamed], []
    )


@pytest.mark.parametrize(
    "behind_line, reason",
    [
        ("", "no behind line at all"),
        ("behind\n", "behind line present but empty"),
        ("behind\npulled  2026-09-05\n", "behind line present but empty"),
        ("behind  \n", "behind line present but empty"),
        ("behind\t\n", "behind line present but empty"),
        ("behind  1\n", "already documented as behind"),
    ],
)
def test_a_behind_line_is_told_from_its_absence(
    checker: ModuleType, behind_line: str, reason: str
) -> None:
    """A bare `behind` key is a line present but empty, not a missing one.

    `trailing-whitespace` rewrites `behind` followed by blanks to the
    bare key, so the bare key is the shape an empty line takes once
    committed.
    [ISS 184](https://github.com/btclib-org/functional-tests/issues/184).
    """
    ledger = _LEDGER.replace("behind  0\n", behind_line)
    assert checker._entries_at_tip(ledger) == ([], [f"`bignum.py` ({reason})"])


def test_a_bare_key_reads_as_that_field_absent(checker: ModuleType) -> None:
    """A bare `commit` or `ref` answers as an absent one does."""
    bare_commit = _LEDGER.replace(f"commit  {_PIN}", "commit")
    assert checker._entries_at_tip(bare_commit) == (
        [],
        ["`bignum.py` (no commit to check against)"],
    )
    bare_ref = _LEDGER.replace("behind", "ref\nbehind")
    entries, skipped = checker._entries_at_tip(bare_ref)
    assert skipped == []
    assert [entry.ref for entry in entries] == [None]
    assert [entry.commit for entry in entries] == [_PIN]


def _failing_gh(
    checker: ModuleType, monkeypatch: pytest.MonkeyPatch, stderrs: list[str]
) -> tuple[list[list[str]], list[int]]:
    """Make `gh` fail with each of `stderrs` in turn, then answer `[]`.

    Returns the argv of every call and the seconds slept between them.
    """
    calls: list[list[str]] = []
    slept: list[int] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if len(calls) <= len(stderrs):
            raise subprocess.CalledProcessError(1, argv, stderr=stderrs[len(calls) - 1])
        return subprocess.CompletedProcess(argv, 0, stdout="[]")

    monkeypatch.setattr(checker.subprocess, "run", run)
    monkeypatch.setattr(checker.time, "sleep", slept.append)
    return calls, slept


@pytest.mark.parametrize(
    "stderr",
    [
        "gh: Not Found (HTTP 404)\n",
        "gh: API rate limit exceeded for user ID 1. (HTTP 403)\n",
        "gh: You have exceeded a secondary rate limit (HTTP 403)\n",
    ],
)
def test_a_failed_gh_call_prints_its_stderr_and_is_not_retried(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stderr: str,
) -> None:
    """A 404 and both rate limits are final: gh's words reach stderr.

    [ISS 446](https://github.com/btclib-org/functional-tests/issues/446).
    """
    calls, slept = _failing_gh(checker, monkeypatch, [stderr])

    with pytest.raises(subprocess.CalledProcessError):
        checker._latest_commit("r", "p")

    assert stderr.strip() in capsys.readouterr().err
    assert len(calls) == 1
    assert slept == []


def test_the_issue_lookup_prints_its_stderr(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The issue lookup goes through the same helper as the commit listing."""
    stderr = "gh: Not Found (HTTP 404)"
    _failing_gh(checker, monkeypatch, [stderr])

    with pytest.raises(subprocess.CalledProcessError):
        checker._open_issue_number("A title")

    assert stderr in capsys.readouterr().err


def test_a_server_error_is_retried_and_the_retry_answers(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A 5xx is waited out, and each failure is printed."""
    stderr = "gh: Bad Gateway (HTTP 502)\n"
    calls, slept = _failing_gh(checker, monkeypatch, [stderr, stderr])

    assert checker._latest_commit("r", "p") is None

    assert len(calls) == 3
    assert slept == [5, 10]
    assert capsys.readouterr().err.count(stderr.strip()) == 2


def test_a_server_error_that_persists_is_raised_after_the_last_attempt(
    checker: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Three attempts in all, and then the error is the caller's."""
    calls, _ = _failing_gh(checker, monkeypatch, ["gh: Bad Gateway (HTTP 502)\n"] * 4)

    with pytest.raises(subprocess.CalledProcessError):
        checker._latest_commit("r", "p")

    assert len(calls) == 3


def test_a_placeholder_path_is_skipped(checker: ModuleType) -> None:
    """A `<name>` in a path is one pin standing in for several files."""
    ledger = _LEDGER.replace(_PATH, "test/functional/data/<name>.json")
    assert checker._entries_at_tip(ledger) == (
        [],
        ["`bignum.py` (one pin serves several files)"],
    )


def test_blocks_skipped_alike_under_one_heading_give_one_line(
    checker: ModuleType,
) -> None:
    """A second block under the same heading adds no second, identical line."""
    block = _LEDGER.removeprefix("### `bignum.py`\n\n").replace("behind  0\n", "")
    ledger = f"### `bignum.py`\n\n{block}\n{block}"
    assert checker._entries_at_tip(ledger) == (
        [],
        ["`bignum.py` (no behind line at all)"],
    )


def test_a_pin_at_its_tip_is_not_drift(
    checker: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """The listing naming the pinned commit itself is a pin still current."""
    ledger = tmp_path / "TF2.md"
    ledger.write_text(_LEDGER, encoding="utf-8")
    fake_gh.commits[_PATH] = (_PIN, "2026-03-24")
    assert checker.find_drift(ledger) == ([], [])


def test_the_issue_body_lists_what_was_skipped(
    checker: ModuleType, tmp_path: Path
) -> None:
    """Each heading this run did not check is named under the drift."""
    drift = checker.Drift(
        checker.Entry("h", "bitcoin/bitcoin", _PATH, _PIN), _REMOVING_SHA, "2019-10-30"
    )
    body = checker._issue_body(tmp_path / "TF2.md", [drift], ["g (no fenced block)"])
    assert body.endswith(
        "\n\nNot checked by this run, for the reason named:\n- g (no fenced block)"
    )
    assert "Not checked" not in checker._issue_body(tmp_path / "TF2.md", [drift], [])


def _drift(checker: ModuleType) -> object:
    """Return one drifted pin, for the tests of `report`."""
    entry = checker.Entry("h", "bitcoin/bitcoin", _PATH, _PIN)
    return checker.Drift(entry, _REMOVING_SHA, "2019-10-30")


def _issue_calls(fake_gh: FakeGh, verb: str) -> list[list[str]]:
    """Return every `gh issue <verb>` call made."""
    return [call[2:] for call in fake_gh.calls if call[1:3] == ["issue", verb]]


def test_report_opens_an_issue_on_drift(
    checker: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """Drift with no issue open creates one, under the title given."""
    checker.report(tmp_path / "TF2.md", "a title", [_drift(checker)], [])
    [create] = _issue_calls(fake_gh, "create")
    assert create[create.index("--title") + 1] == "a title"
    assert _REMOVING_SHA in create[create.index("--body") + 1]


def test_report_edits_the_issue_already_open(
    checker: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """Drift with an issue open rewrites that issue's body."""
    fake_gh.issues = [42]
    checker.report(tmp_path / "TF2.md", "a title", [_drift(checker)], [])
    [edit] = _issue_calls(fake_gh, "edit")
    assert edit[:2] == ["edit", "42"]
    assert _REMOVING_SHA in edit[edit.index("--body") + 1]
    assert _issue_calls(fake_gh, "create") == []


def test_report_closes_the_issue_once_no_pin_drifts(
    checker: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """No drift closes whatever issue was tracking it."""
    fake_gh.issues = [42]
    checker.report(tmp_path / "TF2.md", "a title", [], [])
    [close] = _issue_calls(fake_gh, "close")
    assert close[:2] == ["close", "42"]


def test_report_does_nothing_with_no_drift_and_no_issue(
    checker: ModuleType, fake_gh: FakeGh, tmp_path: Path
) -> None:
    """No drift and no issue open: the search is the only call."""
    checker.report(tmp_path / "TF2.md", "a title", [], [])
    assert [call[1:3] for call in fake_gh.calls] == [["issue", "list"]]


def test_main_prints_each_drift_and_each_skip(
    checker: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--dry-run` prints what it found and touches no issue."""
    gone = "test/functional/test_framework/gone.py"
    ledger = tmp_path / "TF2.md"
    ledger.write_text(
        f"{_LEDGER}\n{_LEDGER.replace('bignum.py', 'gone.py')}\n### `group`\n",
        encoding="utf-8",
    )
    fake_gh.commits[_PATH] = (_REMOVING_SHA, "2019-10-30")
    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), "a title", "--dry-run"])
    assert checker.main() == 0
    out = capsys.readouterr().out
    assert (
        f"BEHIND: `bignum.py` pinned to {_PIN}, latest commit touching it is"
        f" {_REMOVING_SHA} (2019-10-30)"
    ) in out
    assert (
        f"NO COMMIT: `gone.py` pinned to {_PIN}, and no commit on the default"
        f" branch of bitcoin/bitcoin touches {gone}"
    ) in out
    assert "BEHIND: `gone.py`" not in out
    assert "SKIPPED: `group` (no fenced block)" in out
    assert "Every checked pin" not in out
    assert _issue_calls(fake_gh, "list") == []


def test_main_reports_drift_to_the_issue_already_open(
    checker: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without `--dry-run`, drift rewrites the open issue with all it found."""
    ledger = tmp_path / "TF2.md"
    ledger.write_text(f"{_LEDGER}\n### `group`\n", encoding="utf-8")
    fake_gh.commits[_PATH] = (_REMOVING_SHA, "2019-10-30")
    fake_gh.issues = [42]
    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), "a title"])
    assert checker.main() == 0
    [edit] = _issue_calls(fake_gh, "edit")
    assert edit[:2] == ["edit", "42"]
    body = edit[edit.index("--body") + 1]
    assert _REMOVING_SHA in body
    assert "- `group` (no fenced block)" in body
    assert _issue_calls(fake_gh, "create") == []
    assert _issue_calls(fake_gh, "close") == []


def test_main_reports_no_drift_and_closes_the_issue(
    checker: ModuleType,
    fake_gh: FakeGh,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Without `--dry-run`, a clean run says so and closes the open issue."""
    ledger = tmp_path / "TF2.md"
    ledger.write_text(_LEDGER, encoding="utf-8")
    fake_gh.commits[_PATH] = (_PIN, "2026-03-24")
    fake_gh.issues = [42]
    monkeypatch.setattr(sys, "argv", ["prog", str(ledger), "a title"])
    assert checker.main() == 0
    assert "Every checked pin is still at upstream's tip." in capsys.readouterr().out
    assert [close[:2] for close in _issue_calls(fake_gh, "close")] == [["close", "42"]]


@pytest.mark.parametrize(
    "argv", [["prog", "TF2.md"], ["prog", "TF2.md", "a title", "extra"]]
)
def test_main_usage_error(
    checker: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
) -> None:
    """Anything but a path and a title is a usage error, exit 2."""
    monkeypatch.setattr(sys, "argv", argv)
    assert checker.main() == 2
    assert capsys.readouterr().err.startswith("usage: prog <ledger path>")


@pytest.mark.parametrize("dry_run, status", [(False, 2), (True, 0)])
def test_the_script_exits_with_main_s_own_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, dry_run: bool, status: int
) -> None:
    """Run as the workflow step runs it, the exit status is `main`'s.

    A usage error answers 2; a `--dry-run` over a ledger with no fenced
    block calls no `gh` and answers 0. The script runs as its own copy,
    which the `checker` fixture's stand-in does not reach, so
    `subprocess.run` is a `Mock` that raises: a call fails the test
    rather than reaching GitHub. A stub function would leave its body a
    line no green run covers.
    """
    refuse = Mock(side_effect=AssertionError("the script ran a subprocess"))
    monkeypatch.setattr(subprocess, "run", refuse)
    ledger = tmp_path / "TF2.md"
    ledger.write_text("### `group`\n", encoding="utf-8")
    argv = [str(_SCRIPT), str(ledger), "a title", "--dry-run"]
    monkeypatch.setattr(sys, "argv", argv if dry_run else argv[:1])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert excinfo.value.code == status
