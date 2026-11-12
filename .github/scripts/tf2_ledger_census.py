# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

r"""Read Core's `test/functional/` and compare it to TF2.md, in two ways.

`check_vendored_vectors.py` re-checks a pin already in the ledger and
cannot see a file Core *gains*: it walks the ledger's own entries and
asks upstream about each, so a new `test_framework/*.py` is invisible to
it (issue btclib-org/btclib#1751). This script asks the other question:
what does Core's directory hold today, and does every file in it have an
entry here -- in both directions, so a file Core has removed is reported
too, distinctly from one it has gained.

The listing is `git/trees?recursive=1`, checked for `.truncated` rather
than assumed complete, and never `contents`: that endpoint lists a
directory non-recursively, so it would answer without every file under
`crypto/` and report each of them as gone -- the failure mode that reads
as a real finding and is not one.

The second read is the per-test ledger's pins. `check_vendored_vectors.py`
parses only the framework ledger's fenced blocks, so a row of the
per-test table is read here: each Core test file's pin is compared with
that file's last commit at the same tip of Core's default branch. A pin
marked `held` is reported as `TF2.md`'s *The per-test ledger* says.
Every row of the table is read or refused, and a table with no row is
refused, so that a row is never skipped unread.

Each read reports under a title of its own, distinct from the other's
and from `check_vendored_vectors.py`'s per-pin drift title, so a gained
file, a stale framework pin and a stale per-test pin are never the same
issue and one report never overwrites another.

    python .github/scripts/tf2_ledger_census.py TF2.md \
        "test_framework has files TF2.md has no entry for" \
        "TF2.md per-test rows behind upstream"
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

_GH = shutil.which("gh") or "gh"

# gh's stderr for a server error. A secondary rate limit is not
# retried: GitHub asks to wait as long as its retry-after header says,
# which gh's stderr does not carry, or at least a minute.
_TRANSIENT = re.compile(r"HTTP 5\d\d")
_ATTEMPTS = 3


def _gh_output(*args: str) -> str:
    """Return the stdout of a `gh` call, printing gh's stderr where it fails.

    A call failing with a server error is made `_ATTEMPTS` times at
    most, waiting longer before each retry; the last error, or any
    other, is raised.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return subprocess.run(  # noqa: S603
                [_GH, *args],
                capture_output=True,
                check=True,
                encoding="utf-8",
            ).stdout
        except subprocess.CalledProcessError as error:
            print(
                f"gh {' '.join(args)} failed: {error.stderr.strip()}", file=sys.stderr
            )
            if attempt == _ATTEMPTS or not _TRANSIENT.search(error.stderr):
                raise
            time.sleep(5 * attempt)


_UPSTREAM_DIR = "test/functional/test_framework"
_UPSTREAM_REPO = "bitcoin/bitcoin"

_HEADING = re.compile(rf"^### `{re.escape(_UPSTREAM_DIR)}/([^`]+)`$", re.MULTILINE)

_TESTS_DIR = "test/functional"
_PER_TEST_SECTION = "## The per-test ledger"
_PER_TEST_HEADER = ["Core test", "pin", "read at", "bitcoind", "btclib-node"]
_SEPARATOR = re.compile(r"-{3,}")

# a per-test row's first cell: "`<file>.py`", then an optional annotation
_ROW_FILE = re.compile(r"`([\w./-]+\.py)`")

# a per-test row's pin cell, other than `same`: the sha, bare or linked to
# its own commit, then the held marker where the row has one
_PIN_CELL = re.compile(
    r"(?:`(?P<bare>[0-9a-f]{7,40})`"
    r"|\[`(?P<linked>[0-9a-f]{7,40})`\]"
    r"\(https://github\.com/bitcoin/bitcoin/commit/(?P=linked)\))"
    r"(?P<held> held)?"
)

# `repos/{owner}/{repo}/compare/<pin>...<commit>`'s `status` values for a
# commit the pin's history holds: `identical` where it is the pin itself,
# `behind` where it is an ancestor of it
_IN_PIN_HISTORY = frozenset({"identical", "behind"})


def _default_branch_tip(repo: str) -> str:
    """Return the sha the repository's default branch points at.

    :param repo: the repository, `owner/name`.
    :returns: the commit sha.
    """
    return _gh_output("api", f"repos/{repo}/commits/HEAD", "--jq", ".sha").strip()


def upstream_python_files(repo: str, directory: str, sha: str) -> list[str]:
    """List every `.py` path under a directory, at one commit, recursively.

    `git/trees?recursive=1` rather than `contents`, which lists one level
    at a time: a directory carrying a subdirectory would answer without
    what is under it, reporting every one of those paths as gone from
    upstream where they were never listed at all. `.truncated` is
    checked rather than assumed false -- GitHub's own warning that a
    tree over its own limit was cut, silently narrowing every comparison
    below to whatever fit.

    :param repo: the repository the tree belongs to, `owner/name`.
    :param directory: the directory to list, relative to the repository
        root, with no leading or trailing slash.
    :param sha: the commit to list the tree at.
    :returns: each `.py` path under `directory`, relative to it.
    :raises RuntimeError: where the API reports the listing truncated.
    """
    tree = json.loads(
        _gh_output(
            "api",
            "--method",
            "GET",
            f"repos/{repo}/git/trees/{sha}",
            "-f",
            "recursive=1",
        )
    )
    if tree.get("truncated"):
        msg = (
            f"repos/{repo}/git/trees/{sha}?recursive=1 reports truncated: true --"
            " the listing below is a page of the tree, not the whole of it"
        )
        raise RuntimeError(msg)
    prefix = f"{directory}/"
    return sorted(
        entry["path"].removeprefix(prefix)
        for entry in tree["tree"]
        if entry["type"] == "blob"
        and entry["path"].startswith(prefix)
        and entry["path"].endswith(".py")
    )


def ledger_entries(ledger_text: str) -> set[str]:
    """Return the upstream-relative paths TF2.md carries an entry for.

    :param ledger_text: the ledger's own text.
    :returns: each entry's path, relative to `test/functional/test_framework/`.
    """
    return set(_HEADING.findall(ledger_text))


def _open_issue_number(title: str) -> str | None:
    """Return the open issue whose title is exactly `title`, if any.

    The search matches words, not the whole title: `"per-test pins"`
    also finds an issue titled "Twelve more of TF2.md's per-test pins
    are behind upstream". Only an exact title is this script's issue.
    """
    issues = json.loads(
        _gh_output(
            "issue",
            "list",
            "--state",
            "open",
            "--search",
            f'"{title}" in:title',
            "--json",
            "number,title",
        )
    )
    numbers = [str(issue["number"]) for issue in issues if issue["title"] == title]
    return numbers[0] if numbers else None


def _track(title: str, body: str | None, all_clear: str) -> None:
    """Open, update, or close the issue titled `title`.

    :param title: the issue's title, which is also how it is found.
    :param body: the issue's body, or None where there is nothing to
        report, which closes an issue left open.
    :param all_clear: the comment an issue is closed with.
    """
    number = _open_issue_number(title)
    if body is None:
        if number is not None:
            subprocess.run(  # noqa: S603
                [_GH, "issue", "close", number, "--comment", all_clear],
                check=True,
            )
        return
    if number is None:
        subprocess.run(  # noqa: S603
            [_GH, "issue", "create", "--title", title, "--body", body],
            check=True,
        )
    else:
        subprocess.run(  # noqa: S603
            [_GH, "issue", "edit", number, "--body", body], check=True
        )


def _issue_body(sha: str, gained: list[str], removed: list[str]) -> str:
    lines = [
        f"`test/functional/test_framework/` at `bitcoin/bitcoin@{sha[:12]}`",
        "disagrees with `TF2.md`'s own census. Deciding an entry for a",
        "gained file, or removing one for a file upstream no longer has,",
        "is not this report's to do.",
        "",
    ]
    if gained:
        lines.append("Gained upstream, no entry in TF2.md:")
        lines.extend(f"- `{path}`" for path in gained)
        lines.append("")
    if removed:
        lines.append("An entry in TF2.md, gone from upstream:")
        lines.extend(f"- `{path}`" for path in removed)
    return "\n".join(lines)


def report(title: str, sha: str, gained: list[str], removed: list[str]) -> None:
    """Open, update, or close the census's tracking issue.

    :param title: the issue title, distinct from the per-pin drift
        issue's own so that the two never share one.
    :param sha: the commit the census was read at.
    :param gained: paths upstream has that no entry names.
    :param removed: paths an entry names that upstream no longer has.
    """
    _track(
        title,
        _issue_body(sha, gained, removed) if gained or removed else None,
        f"Re-read at `{sha[:12]}`: TF2.md's census agrees with upstream.",
    )


@dataclass(frozen=True)
class Pin:
    """A Core test file's pin in the per-test ledger.

    :param sha: the pin, abbreviated as the table writes it.
    :param held: whether the pin cell carries the `held` marker.
    """

    sha: str
    held: bool


@dataclass(frozen=True)
class Drift:
    """A per-test pin that is not at its path's tip upstream.

    :param path: the Core test file, relative to `test/functional/`.
    :param pin: its pin in the per-test ledger.
    :param tip: the path's last commit upstream, as `(sha, date)`, or None
        where no commit upstream touches the path.
    """

    path: str
    pin: Pin
    tip: tuple[str, str] | None

    def line(self) -> str:
        """Return the drift as one line: the path, the pin and the tip."""
        tip = f"`{self.tip[0][:12]}` ({self.tip[1]})" if self.tip else "no commit"
        return f"`{self.path}`: `{self.pin.sha}` -> {tip}"


def _table_rows(section: str) -> list[list[str]]:
    """Return the cells of every row of the section's table.

    :param section: the per-test section's own text.
    :returns: each `|` line's trimmed cells, less the header and the
        separator.
    :raises ValueError: where a row is not five cells wide.
    """
    rows = []
    for line in section.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells == _PER_TEST_HEADER or all(map(_SEPARATOR.fullmatch, cells)):
            continue
        if len(cells) != len(_PER_TEST_HEADER):
            msg = f"a row of {len(cells)} cells, not five: {line!r}"
            raise ValueError(msg)
        rows.append(cells)
    return rows


def _row_pin(cells: list[str]) -> tuple[str, Pin | None]:
    """Return a row's Core test file, and its pin, None for `same`.

    :param cells: the row's trimmed cells.
    :raises ValueError: where the first or the pin cell is in no shape
        this reads.
    """
    file_match = _ROW_FILE.match(cells[0])
    if file_match is None:
        msg = f"no Core test file in {cells[0]!r}"
        raise ValueError(msg)
    if cells[1] == "same":
        return file_match.group(1), None
    pin_match = _PIN_CELL.fullmatch(cells[1])
    if pin_match is None:
        msg = f"{cells[0]}: no pin reads {cells[1]!r}"
        raise ValueError(msg)
    sha = pin_match["bare"] or pin_match["linked"]
    return file_match.group(1), Pin(sha, pin_match["held"] is not None)


def per_test_pins(ledger_text: str) -> dict[str, Pin]:
    """Return each Core test file the per-test ledger names, with its pin.

    A pin cell reading `same` repeats the nearest row above it naming the
    same file, `TF2.md`'s own rule for that table.

    :param ledger_text: `TF2.md`'s own text.
    :returns: each Core test file, relative to `test/functional/`, against
        its pin.
    :raises ValueError: where the section is missing or has no row, where
        a row is not five cells wide, where its first or pin cell is in no
        shape this reads, where `same` has no row above it, or where two
        rows pin one file differently.
    """
    if _PER_TEST_SECTION not in ledger_text:
        msg = f"no {_PER_TEST_SECTION!r} section"
        raise ValueError(msg)
    section = ledger_text.split(_PER_TEST_SECTION, 1)[1].split("\n## ", 1)[0]
    pins: dict[str, Pin] = {}
    for cells in _table_rows(section):
        path, pin = _row_pin(cells)
        if pin is None:
            if path not in pins:
                msg = f"{cells[0]}: `same` with no row above it naming {path}"
                raise ValueError(msg)
        elif pins.setdefault(path, pin) != pin:
            msg = f"{cells[0]}: {pin} where a row above pins {pins[path]}"
            raise ValueError(msg)
    if not pins:
        msg = f"no row under {_PER_TEST_SECTION!r}"
        raise ValueError(msg)
    return pins


def _latest_commit(repo: str, path: str, sha: str) -> tuple[str, str] | None:
    """Return the sha and date of the last commit touching a path.

    :param repo: the repository, `owner/name`.
    :param path: the path, relative to the repository root.
    :param sha: the commit whose history is read.
    :returns: the commit's sha and its committer date's first ten
        characters, or None where no commit in that history touches the
        path.
    """
    if not (
        commits := json.loads(
            _gh_output(
                "api",
                "--method",
                "GET",
                f"repos/{repo}/commits",
                "-f",
                f"path={path}",
                "-f",
                f"sha={sha}",
                "-f",
                "per_page=1",
            )
        )
    ):
        return None
    return commits[0]["sha"], commits[0]["commit"]["committer"]["date"][:10]


def _in_pin_history(repo: str, pin: str, commit: str) -> bool:
    """Say whether a commit is the pin itself or one of its ancestors.

    A committer date does not answer it: a commit dated before the pin
    can land after it, through a merge the pin's history does not hold.

    :param repo: the repository, `owner/name`.
    :param pin: the pin, an abbreviated sha.
    :param commit: a full sha.
    :returns: True where `compare` answers `identical` or `behind`.
    """
    status = _gh_output(
        "api",
        "--method",
        "GET",
        f"repos/{repo}/compare/{pin}...{commit}",
        "-f",
        "per_page=1",
        "--jq",
        ".status",
    )
    return status.strip() in _IN_PIN_HISTORY


def pin_drift(repo: str, pins: dict[str, Pin], sha: str) -> list[Drift]:
    """Return each pin not at its path's tip, held or not.

    :param repo: the repository the pins are commits of, `owner/name`.
    :param pins: `per_test_pins`'s answer.
    :param sha: the commit whose history each path's tip is read in.
    :returns: one `Drift` per pin whose path has a commit the pin's
        history does not hold, or no commit at all, sorted by path.
    """
    drift = []
    for path, pin in sorted(pins.items()):
        tip = _latest_commit(repo, f"{_TESTS_DIR}/{path}", sha)
        if tip is not None and (
            tip[0].startswith(pin.sha) or _in_pin_history(repo, pin.sha, tip[0])
        ):
            continue
        drift.append(Drift(path, pin, tip))
    return drift


def _pins_issue_body(sha: str, behind: list[Drift], stale: list[str]) -> str:
    lines = [
        f"`TF2.md`'s per-test ledger, read at `bitcoin/bitcoin@{sha[:12]}`.",
        "Moving a pin means reading its port against the new revision",
        "first, which is not this report's to do.",
        "",
    ]
    if behind:
        lines.append("Behind the path's last commit upstream:")
        lines.extend(f"- {drift.line()}" for drift in behind)
        lines.append("")
    if stale:
        lines.append("Marked `held`, and at the path's last commit upstream:")
        lines.extend(f"- `{path}`" for path in stale)
    return "\n".join(lines)


def report_pins(title: str, sha: str, behind: list[Drift], stale: list[str]) -> None:
    """Open, update, or close the per-test pins' tracking issue.

    :param title: the issue title, distinct from the census's own.
    :param sha: the commit the pins were read at.
    :param behind: each pin the issue lists as behind upstream.
    :param stale: each path the issue lists as held at its tip.
    """
    _track(
        title,
        _pins_issue_body(sha, behind, stale) if behind or stale else None,
        f"Re-read at `{sha[:12]}`: every per-test pin in TF2.md is at its"
        " path's tip or held.",
    )


def main() -> int:
    """Run both reads against one tip, report each, and say so on stdout.

    --dry-run skips opening, updating or closing either issue, the same
    switch `check_vendored_vectors.py` takes and for the same reason: a
    change to this script or to the ledger is exercised without editing
    whatever tracking issue happens to be open at the time.
    """
    args = [a for a in sys.argv[1:] if a != "--dry-run"]
    dry_run = len(args) != len(sys.argv) - 1
    if len(args) != 3 or args[1] == args[2]:
        print(
            f"usage: {Path(sys.argv[0]).name} <ledger path> <census issue title>"
            " <per-test pins issue title> [--dry-run]",
            file=sys.stderr,
        )
        return 2
    ledger_path, title, pins_title = Path(args[0]), args[1], args[2]
    ledger_text = ledger_path.read_text(encoding="utf-8")
    pins = per_test_pins(ledger_text)

    sha = _default_branch_tip(_UPSTREAM_REPO)
    upstream = set(upstream_python_files(_UPSTREAM_REPO, _UPSTREAM_DIR, sha))
    entries = ledger_entries(ledger_text)

    gained = sorted(upstream - entries)
    removed = sorted(entries - upstream)

    for path in gained:
        print(f"GAINED: {path}")
    for path in removed:
        print(f"REMOVED: {path}")
    if not gained and not removed:
        print(f"TF2.md's census agrees with upstream at {sha[:12]}.")
    if not dry_run:
        report(title, sha, gained, removed)

    drift = pin_drift(_UPSTREAM_REPO, pins, sha)
    behind = [d for d in drift if not d.pin.held or d.tip is None]
    drifted = {d.path for d in drift}
    stale = [
        path for path, pin in sorted(pins.items()) if pin.held and path not in drifted
    ]
    for d in drift:
        print(f"{'HELD' if d.pin.held else 'BEHIND'}: {d.line()}")
    for path in stale:
        print(f"HELD AT TIP: {path}")
    if not behind and not stale:
        print(f"Every per-test pin in TF2.md is at its tip or held at {sha[:12]}.")
    if not dry_run:
        report_pins(pins_title, sha, behind, stale)
    return 0


if __name__ == "__main__":
    sys.exit(main())
