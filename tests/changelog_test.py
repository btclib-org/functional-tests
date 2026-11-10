# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""What CHANGELOG.md must not say about itself.

A count of its own entries is the one fact in the file nothing derives:
prose can be reviewed, a number is right or wrong invisibly. Checking
the number against the entries would make it a line every open branch
has to edit, so the file states none, and this module fails on a count.

RELEASE_NOTES.md is not read here: section 2 of the organization's
standard gives it to a repository that publishes, and this tree is
tier 2, owing no release (CONTRIBUTING.md's *A version, and no
release*).

A test rather than a reading because two branches moving a count to the
same number merge in silence, with nothing in the merge output to say so.
"""

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[1]
_FILES = (_ROOT / "CHANGELOG.md",)

# a count of the file's own entries, spelled in digits or in words,
# `\s+` covering an 80-column wrap wherever it falls
_NUMBER = r"(?:\d+|(?:[a-z]+-)?(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|[a-z]+teen|[a-z]+ty|hundred(?:\s+and\s+[a-z-]+)?))"
_FORBIDDEN = (rf"(?i)\b{_NUMBER}\s+entries\b",)

_RESURRECTED = (
    "A hundred and eighty entries, grouped.",
    "the largest: a hundred and eighty\nentries",
    "This section holds 12 entries.",
)


@pytest.mark.parametrize("path", _FILES, ids=lambda p: p.name)
def test_the_file_states_no_count(path: Path) -> None:
    """No entry count."""
    text = path.read_text(encoding="utf-8")
    found = [m[0] for p in _FORBIDDEN for m in re.finditer(p, text)]
    assert not found, f"{path.name} states a count: {found!r}"


def test_the_patterns_still_match() -> None:
    """The guard above passes for free if its patterns match nothing."""
    for text in _RESURRECTED:
        assert any(re.search(p, text) for p in _FORBIDDEN), text
    for pattern in _FORBIDDEN:
        assert any(re.search(pattern, t) for t in _RESURRECTED), pattern
