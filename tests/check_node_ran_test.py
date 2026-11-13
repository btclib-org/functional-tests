# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""Tests for the node-ran check of `.github/scripts`.

The script is loaded by path, `.github/scripts` being no package, the
same idiom `tf2_master_verdict_test.py` uses for its own script. Each
report is written by hand in the shape `pytest --junitxml` gives a
passing and a skipped testcase: the skip's reason is the `message`
attribute of its `skipped` element.
"""

from __future__ import annotations

import importlib.util
import runpy
import sys
from pathlib import Path
from types import ModuleType

import pytest

from btclib_functional_tests.capability import Capability
from btclib_functional_tests.wallet import WalletCapability

_SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "check_node_ran.py"

_NO_NODE = (
    "no target node: no btclib_node importable by /usr/bin/python3: install it"
    " there, or name another interpreter in TF2_BTCLIB_NODE_PYTHON"
)


@pytest.fixture
def check(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    """Return the script, imported by path, registered before it runs."""
    spec = importlib.util.spec_from_file_location("check_node_ran", _SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "check_node_ran", module)
    spec.loader.exec_module(module)
    return module


def _report(tmp_path: Path, cases: list[tuple[str, str | None]]) -> Path:
    """Write a JUnit report: one testcase per (name, skip message or None)."""
    body = []
    for name, message in cases:
        if message is None:
            body.append(f'<testcase classname="m" name="{name}" time="0.1" />')
        else:
            body.append(
                f'<testcase classname="m" name="{name}" time="0.0">'
                f'<skipped type="pytest.skip" message="{message}">'
                f"m.py:1: {message}</skipped></testcase>"
            )
    path = tmp_path / "integration.xml"
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests">'
        f'<testsuite name="pytest">{"".join(body)}</testsuite></testsuites>',
        encoding="utf-8",
    )
    return path


def test_every_capability_has_its_message(check: ModuleType) -> None:
    """Every capability contributes the message `require` refuses it with."""
    messages = check.capability_messages()
    assert len(messages) == len(Capability) + len(WalletCapability)
    for capability in Capability:
        assert f"node does not declare {capability.value}" in messages
    for wallet_capability in WalletCapability:
        assert f"wallet does not declare {wallet_capability.value}" in messages


def test_a_capability_skip_is_not_counted(check: ModuleType, tmp_path: Path) -> None:
    """A skip for an undeclared capability reached the node, and is exempt."""
    report = _report(
        tmp_path,
        [("ran", None), ("mines", f"node does not declare {Capability.MINE.value}")],
    )
    assert check.unreached(report) == (2, [])


def test_a_missing_node_skip_is_counted(check: ModuleType, tmp_path: Path) -> None:
    """A skip for want of a node is counted, with its name and its message."""
    report = _report(tmp_path, [("ran", None), ("unreached", _NO_NODE)])
    assert check.unreached(report) == (2, [("unreached", _NO_NODE)])


def test_main_usage_error(check: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    """Anything but one argument is a usage error, exit 2."""
    monkeypatch.setattr(sys, "argv", ["check_node_ran.py"])
    assert check.main() == 2


def test_main_fails_on_a_missing_node_skip(
    check: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing-node skip fails the run and is annotated as an error."""
    report = _report(tmp_path, [("ran", None), ("unreached", _NO_NODE)])
    monkeypatch.setattr(sys, "argv", ["check_node_ran.py", str(report)])
    assert check.main() == 1
    out = capsys.readouterr().out
    assert f"::error::unreached skipped: {_NO_NODE}" in out
    assert "::error::2 test(s) collected, 1 skipped" in out


def test_main_fails_on_an_empty_report(
    check: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A report with no testcase at all fails the run."""
    monkeypatch.setattr(sys, "argv", ["check_node_ran.py", str(_report(tmp_path, []))])
    assert check.main() == 1


def test_main_passes_with_only_capability_skips(
    check: ModuleType,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Passes and capability skips alone are a run that reached its node."""
    report = _report(
        tmp_path,
        [("ran", None), ("mines", f"node does not declare {Capability.MINE.value}")],
    )
    monkeypatch.setattr(sys, "argv", ["check_node_ran.py", str(report)])
    assert check.main() == 0
    out = capsys.readouterr().out
    assert "none skipped but for a capability the node or the wallet" in out


def test_the_script_exits_with_main_s_own_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run as the workflow step runs it, the exit status is `main`'s."""
    monkeypatch.setattr(sys, "argv", [str(_SCRIPT)])
    with pytest.raises(SystemExit) as excinfo:
        runpy.run_path(str(_SCRIPT), run_name="__main__")
    assert excinfo.value.code == 2
