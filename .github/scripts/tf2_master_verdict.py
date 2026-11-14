# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

r"""Classify a failure that appears only against Core built from `master`.

`node-integration.yml`'s `core-master` job runs the `*_bitcoind_test.py`
half of `tests/integration` twice in one workflow run: once against the
pinned release (the required `bitcoind` job, whose own JUnit report it
downloads) and once against a `bitcoind` this repository built from
Core's `master` itself. A test failing in the second report and not in
the first is measured the way `ISS 8`'s decision of 2026-09-25 lays down,
`TF2.md`'s pin for its Core test file against that file's last commit on
`master`, and this script names the outcome one of two ways:

- **stale port** -- the Core test file `TF2.md`'s per-test ledger pins
  the failing test to, or a data file it loads, has moved since that
  pin. The defect is this repository's own: the port was read from a
  revision upstream has since changed, and needs a fresh reading rather
  than a report to Core. The report line names the path that moved.
- **file unchanged since the pin** -- neither the file nor a data file it
  loads has a commit on `master` since the pin, and this names neither a
  cause nor an owner. Core's behaviour may have changed under that file,
  or under a different file; the failure may not reproduce; or
  the port may never have matched the file it cites: the required job
  passes such a port wherever the pinned release predates a behaviour
  change the pin already carries, and only `master` fails it
  ([ISS 143](https://github.com/btclib-org/functional-tests/issues/143)).
  A pin compared with the file's last commit tells none of these apart,
  so the port is checked by hand against the pinned file's own
  expectation first.

The Core file a failing test names is read from the failing module's own
docstring rather than guessed from its filename: `p2p_invalid_messages_
bitcoind_test.py` and `p2p_invalid_messages_misbehaving_bitcoind_test.py`
both port `p2p_invalid_messages.py`, so a module-name transform would
have to special-case exactly the pairing this avoids by reading what
every `*_bitcoind_test.py` module already states in its own first
paragraph, "Read from Core's `test/functional/<file>.py`" -- the same
sentence a human re-checking a row of `TF2.md`'s per-test ledger by hand
would read. Once the Core file is named, its pin comes from that
ledger's own table rather than from the module's docstring a second
time: the table is what a re-check by hand moves, where the docstring
is prose nothing here re-derives automatically.

The data files a Core test loads are `_DATA_FILES`, stated here rather
than parsed out of Core's file, which reaches them in more than one way:
an argument default, an `open()` beside `__file__`, a `from data import`.
Run from this tree, with `<core>` a Core checkout, this prints each cited
Core test beside each file of `test/functional/data/` whose name, less
its suffix, the test mentions as a word, which is what `_DATA_FILES`
holds; the package marker and the README are left out, `__init__` being
a method name as well. `awk` keeps the directory's own files, so
`data/util/` and what it holds are not read; no cited test reads it:

    git grep -h -o "Read from Core's \`test/functional/[^\`]*\`" \
            -- 'tests/integration/*_bitcoind_test.py' \
        | sed -E 's/.*functional\/([^`]*)`/\1/' | sort -u \
        | while read -r f; do
            git -C <core> ls-tree origin/master test/functional/data/ \
                | awk '$2 == "blob" {print $4}' \
                | grep -v -E '/(__init__\.py|README\.md)$' \
                | while read -r d; do
                    n=$(basename "${d%.*}")
                    if git -C <core> show "origin/master:test/functional/$f" \
                            | grep -q -w -- "$n"; then
                        echo "$f ${d#test/functional/}"
                    fi
                done
        done

A path has moved since the pin where its last commit on `master` is
neither the pin itself nor in the pin's own history, which
`repos/bitcoin/bitcoin/compare/<pin>...<commit>` answers as its `status`.
Committer dates do not answer it: a commit dated before the pin can land
on `master` after it, through a merge the pin's history does not hold.

This script opens no issue. Per master-only failure it reaches the
network once for the Core test file and once for each data file it
loads, for that path's last commit on `master`
(`repos/bitcoin/bitcoin/commits`, `check_vendored_vectors.py`'s own
`_latest_commit` in miniature), and once more for each path whose last
commit is not the pin, for the comparison -- everything else is read
from the two JUnit reports and from `TF2.md` on disk. What it prints is
the whole report: the workflow step redirects it into `$GITHUB_STEP_SUMMARY`
rather than this script writing there itself, matching neither of the
two scripts beside it needing to know the runner is there at all.

    uv run --no-project python .github/scripts/tf2_master_verdict.py \
        TF2.md master.xml pinned.xml
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
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


_UPSTREAM_REPO = "bitcoin/bitcoin"

# a `*_bitcoind_test.py` module's own first paragraph, every one of them
# (measured against every module the tree carries at the time this was
# written): "Read from Core's `test/functional/<file>.py`"
_CITATION = re.compile(r"Read from Core's `test/functional/([\w./-]+\.py)`")

# a per-test ledger row's own leading cell: "`<file>.py`", with an
# optional parenthetical the row's own annotation carries and this does
# not need -- several rows can share one Core file, one wire and one log
# half of the same Core assertion among them
_ROW_FILE = re.compile(r"^`([\w./-]+\.py)`")

_PIN = re.compile(r"[0-9a-f]{8,40}")

_UNCHANGED = "file unchanged since the pin"

# each cited Core test file loading a file of `test/functional/data/`,
# against those files, relative to `test/functional/`: the census
# command in the module docstring is what fills it, measured at Core's
# `b13ba1a7d3`
_DATA_FILES: dict[str, tuple[str, ...]] = {
    "feature_block.py": ("data/invalid_txs.py",),
    "rpc_createmultisig.py": ("data/rpc_bip67.json",),
    "rpc_getblockstats.py": ("data/rpc_getblockstats.json",),
}

# `repos/{owner}/{repo}/compare/<pin>...<commit>`'s own `status` values
# for a commit the pin's history holds: `identical` where it is the pin
# itself, `behind` where it is an ancestor of it
_IN_PIN_HISTORY = frozenset({"identical", "behind"})


def _cells(row: str) -> list[str]:
    """Split one `| a | b | c |` table row into its trimmed cells.

    :param row: the row, including its leading and trailing `|`.
    :returns: each cell, in order, with its surrounding whitespace
        stripped.
    """
    return [cell.strip() for cell in row.strip().strip("|").split("|")]


def ledger_pins(ledger_text: str) -> dict[str, str]:
    """Return each Core test file the per-test ledger names, with its pin.

    A `pin` cell reading `same` repeats the nearest row above it naming
    the same Core test file, `TF2.md`'s own rule for that table; the
    first, explicit occurrence is what this keeps, every `same` after it
    changing nothing this needs.

    :param ledger_text: `TF2.md`'s own text.
    :returns: each Core test file the table names, against the commit its
        first row pins it to.
    """
    pins: dict[str, str] = {}
    for line in ledger_text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = _cells(line)
        file_match = _ROW_FILE.match(cells[0])
        if file_match is None or file_match.group(1) in pins:
            continue
        pin_match = _PIN.search(cells[1])
        if pin_match is not None:
            pins[file_match.group(1)] = pin_match.group()
    return pins


def parse_junit(path: Path) -> dict[str, str]:
    """Return `{"classname::name": status}` for every testcase a report holds.

    :param path: the JUnit XML report's own path.
    :returns: `"fail"` where a `<failure>` or an `<error>` child is
        present, `"skip"` where a `<skipped>` one is, `"pass"` otherwise.
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
        results[key] = status
    return results


def master_only_failures(master: dict[str, str], pinned: dict[str, str]) -> list[str]:
    """Return each test key failing against master but not against the pin.

    :param master: `parse_junit`'s reading of the master-built run.
    :param pinned: `parse_junit`'s reading of the pinned-release run.
    :returns: the failing keys, sorted.
    """
    return sorted(
        key
        for key, status in master.items()
        if status == "fail" and pinned.get(key) != "fail"
    )


def core_file_for_test(classname: str, root: Path) -> str | None:
    """Return the Core test file a `*_bitcoind_test.py` module cites.

    :param classname: the JUnit `classname`, a dotted module path such as
        `tests.integration.feature_blocksdir_bitcoind_test`.
    :param root: the directory the dotted path resolves under.
    :returns: the path relative to `test/functional/` the module's own
        docstring names, or None where the module cannot be read or
        names none.
    """
    module_path = root.joinpath(*classname.split(".")).with_suffix(".py")
    try:
        text = module_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = _CITATION.search(text)
    return match.group(1) if match else None


def _latest_commit(path: str) -> tuple[str, str] | None:
    """Return the sha and date of the most recent commit touching a Core path.

    :param path: the path, relative to `bitcoin/bitcoin`'s own root
        (`test/functional/<file>.py`, or a data file under
        `test/functional/data/`).
    :returns: the commit sha and the committer date's first ten
        characters, or None where upstream has no commit touching that
        path at all, a path `master` never held. A path deleted or
        renamed away answers with the commit that removed it, which the
        pin's history does not hold, so it reads as moved.
    """
    commits = json.loads(
        _gh_output(
            "api",
            "--method",
            "GET",
            f"repos/{_UPSTREAM_REPO}/commits",
            "-f",
            f"path={path}",
            "-f",
            "per_page=1",
        )
    )
    if not commits:
        return None
    commit = commits[0]
    sha: str = commit["sha"]
    date: str = commit["commit"]["committer"]["date"][:10]
    return sha, date


def _in_pin_history(pin: str, commit: str) -> bool:
    """Say whether a commit is the pin itself or one of its ancestors.

    :param pin: `TF2.md`'s own pin, an abbreviated sha.
    :param commit: a full sha `_latest_commit` named.
    :returns: True where `repos/bitcoin/bitcoin/compare` answers the
        commit as the pin or behind it, False where the commit is ahead
        of the pin or on a line the pin's history does not hold.
    """
    status = _gh_output(
        "api",
        "--method",
        "GET",
        f"repos/{_UPSTREAM_REPO}/compare/{pin}...{commit}",
        "-f",
        "per_page=1",
        "--jq",
        ".status",
    )
    return status.strip() in _IN_PIN_HISTORY


def has_moved(pin: str, latest_commit: str) -> bool:
    """Say whether a Core path has a commit on `master` since the pin.

    :param pin: `TF2.md`'s own pin for the Core test file, from
        `ledger_pins` -- an abbreviated sha.
    :param latest_commit: the sha `_latest_commit` names as the path's
        tip on `master` -- the API's own full sha, which the pin is a
        prefix of where that commit is the pin itself.
    :returns: False where the commit is the pin, with no call made, or in
        the pin's own history; True otherwise.
    """
    if latest_commit.startswith(pin):
        return False
    return not _in_pin_history(pin, latest_commit)


def verdicts(
    ledger_text: str, master: dict[str, str], pinned: dict[str, str], root: Path
) -> list[str]:
    """Return one report line per master-only failure.

    :param ledger_text: `TF2.md`'s own text.
    :param master: `parse_junit`'s reading of the master-built run.
    :param pinned: `parse_junit`'s reading of the pinned-release run.
    :param root: the directory a JUnit `classname` resolves under.
    :returns: one line per master-only failure, naming the Core file, its
        pin and this repository's own verdict -- with each path that moved
        since the pin, where one did -- or the reason no verdict could be
        reached.
    """
    pins = ledger_pins(ledger_text)
    lines = []
    for key in master_only_failures(master, pinned):
        core_file = core_file_for_test(key.split("::", 1)[0], root)
        if core_file is None:
            lines.append(f"- `{key}`: no Core file citation found in its own module")
            continue
        pin = pins.get(core_file)
        if pin is None:
            lines.append(
                f"- `{key}` (`{core_file}`): TF2.md's per-test ledger names no row"
                " for it"
            )
            continue
        data_files = _DATA_FILES.get(core_file, ())
        latest: dict[str, tuple[str, str]] = {}
        gone = None
        for path in (core_file, *data_files):
            found = _latest_commit(f"test/functional/{path}")
            if found is None:
                gone = path
                break
            latest[path] = found
        if gone is not None:
            lines.append(
                f"- `{key}` (`{core_file}`, pinned `{pin}`): `{_UPSTREAM_REPO}` has"
                f" no commit touching `{gone}` at all"
            )
            continue
        if moved := [path for path, (sha, _) in latest.items() if has_moved(pin, sha)]:
            changes = "; ".join(
                f"`{path}` last changed on master in `{latest[path][0]}`"
                f" ({latest[path][1]})"
                for path in moved
            )
            lines.append(
                f"- `{key}` (`{core_file}`): **stale port** -- TF2.md pins"
                f" `{pin}`, and {changes}"
            )
            continue
        latest_commit, latest_date = latest[core_file]
        line = (
            f"- `{key}` (`{core_file}`): **{_UNCHANGED}** -- TF2.md pins `{pin}`,"
            f" its last commit on master is `{latest_commit}` ({latest_date})"
        )
        if data_files:
            names = ", ".join(f"`{path}`" for path in data_files)
            line += f", and no commit since the pin touches {names}, which it loads"
        line += (
            ": the cause is not measured, and the port may never have"
            f" matched the file; check the port against `{core_file}` at"
            f" `{pin}` first"
        )
        lines.append(line)
    return lines


def main() -> int:
    """Compare the two reports, print the verdict, and say so on stdout.

    A usage error is the only way this exits 2, the workflow step passing
    all three arguments every time.
    """
    args = sys.argv[1:]
    if len(args) != 3:
        print(
            f"usage: {Path(sys.argv[0]).name} <TF2.md> <master junit.xml>"
            " <pinned junit.xml>",
            file=sys.stderr,
        )
        return 2
    ledger_path, master_path, pinned_path = (Path(a) for a in args)
    master = parse_junit(master_path)
    pinned = parse_junit(pinned_path)
    lines = verdicts(
        ledger_path.read_text(encoding="utf-8"), master, pinned, Path.cwd()
    )
    if not lines:
        print(
            "No test fails against Core's master that does not already fail"
            " against the pinned release."
        )
        return 0
    print("Failing only against Core's master, not against the pinned release:")
    print()
    for line in lines:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
