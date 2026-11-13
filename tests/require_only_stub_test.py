# Copyright (c) The btclib developers
# Distributed under the MIT software license, see the accompanying
# LICENSE file or https://opensource.org/license/mit for the full text.

"""A require-only test ends in `pytest.fail`, and asks for what its twin does.

[ISS 82](https://github.com/btclib-org/functional-tests/issues/82): a
test whose body is only `del` statements and `require(...)` calls passes
the moment the node, or the wallet factory, under test declares every
capability it names, without the scenario its own name claims ever
running. This module is the AST walk that holds every function under
`tests/integration/` to not being that shape -- parsed rather than
imported, since the files it reads skip themselves without
`TF2_INTEGRATION=1`. A stub that still ends in `del`/`require` alone is
the defect this file exists to catch; one that ends in a terminal
`pytest.fail(...)` call is not, whatever `require` decides once the node
or the wallet factory it addresses declares more than it does today.

A stub also drifts from the twin it stands in for: the second walk below
compares a `*_btclib_node_test.py` function's own ordered
`require(...)` capabilities against the `*_bitcoind_test.py` function of
the same name, where one exists, and holds the two to being identical --
no allowance for a capability the stub happens to have dropped, added out
of order, or never asked for at all.
"""

from __future__ import annotations

import ast
from pathlib import Path

_INTEGRATION = Path(__file__).parent / "integration"


def _strip_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    """Return `body` with a leading bare string literal removed, if any."""
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
    ):
        return body[1:]
    return body


def _is_del_or_require(stmt: ast.stmt) -> bool:
    """Return whether `stmt` is a `del` or a bare `require(...)` call."""
    return isinstance(stmt, ast.Delete) or (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and getattr(stmt.value.func, "id", "") == "require"
    )


def _is_fail_call(stmt: ast.stmt) -> bool:
    """Return whether `stmt` is a bare `pytest.fail(...)` call."""
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Call)
        and ast.unparse(stmt.value.func) == "pytest.fail"
    )


def _classify(fn: ast.FunctionDef) -> str:
    """Return what shape `fn`'s own body, docstring aside, takes.

    `"bare"` is [ISS 82](https://github.com/btclib-org/functional-tests/issues/82)'s
    own defect: nothing but `del`/`require`, with no terminal
    `pytest.fail`. `"stub"` is the same body ended in one. Anything else
    -- a real assertion, an empty body -- is `"other"` and is neither a
    stub nor a defect this module has an opinion on.
    """
    body = _strip_docstring(fn.body)
    if not body:
        return "other"
    if _is_fail_call(body[-1]) and all(_is_del_or_require(s) for s in body[:-1]):
        return "stub"
    return "bare" if all(_is_del_or_require(s) for s in body) else "other"


def _required_capabilities(fn: ast.FunctionDef) -> tuple[str, ...]:
    """Return every `require(...)` call's own first argument, in source order.

    `ast.walk` is breadth-first, so a `require` nested deeper than a later
    one would come out after it; sorting on the call's own position puts
    the calls back in the order they run.
    """
    calls = sorted(
        (
            node
            for node in ast.walk(fn)
            if isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "require"
            and node.args
        ),
        key=lambda node: (node.lineno, node.col_offset),
    )
    return tuple(ast.unparse(node.args[0]) for node in calls)


def _test_functions(path: Path) -> dict[str, ast.FunctionDef]:
    """Return `path`'s own top-level `test_*` functions, by name."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {
        node.name: node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    }


def _offenders(root: Path) -> list[str]:
    """Return one "file.py::name" per "bare" function found under `root`."""
    return [
        f"{path.name}::{name}"
        for path in sorted(root.glob("*_test.py"))
        for name, fn in _test_functions(path).items()
        if _classify(fn) == "bare"
    ]


def _mismatches(root: Path) -> list[str]:
    """Return one line per stub whose capabilities drift from its twin's.

    Compared only where a same-named function exists on both sides: a
    stub file with no `*_bitcoind_test.py` twin at all, or one whose twin
    names no function of that name, names nothing to compare against.
    """
    mismatches = []
    for path in sorted(root.glob("*_btclib_node_test.py")):
        twin = path.with_name(
            path.name.replace("_btclib_node_test.py", "_bitcoind_test.py")
        )
        if not twin.exists():
            continue
        twin_fns = _test_functions(twin)
        for name, fn in _test_functions(path).items():
            if _classify(fn) != "stub":
                continue
            twin_fn = twin_fns.get(name)
            if twin_fn is None:
                continue
            stub_caps = _required_capabilities(fn)
            twin_caps = _required_capabilities(twin_fn)
            if stub_caps != twin_caps:
                mismatches.append(
                    f"{path.name}::{name} asks for {stub_caps}, "
                    f"{twin.name}::{name} asks for {twin_caps}"
                )
    return mismatches


def test_no_require_only_stub_is_missing_its_terminal_fail() -> None:
    """Every del/require-only test under tests/integration/ ends in a fail."""
    offenders = _offenders(_INTEGRATION)
    assert not offenders, "require-only, with no terminal pytest.fail: " + ", ".join(
        offenders
    )


def test_every_stubs_capabilities_match_its_twins() -> None:
    """Every stub asks for what its bitcoind twin does, in the same order."""
    mismatches = _mismatches(_INTEGRATION)
    assert not mismatches, "\n".join(mismatches)


def test_offenders_flags_a_del_require_only_body(tmp_path: Path) -> None:
    """A body of nothing but `del`/`require` is `"bare"`, with no fail."""
    (tmp_path / "x_btclib_node_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    del node\n"
        "    require(Capability.MINE, node.capabilities, skip_counts)\n"
    )
    assert _offenders(tmp_path) == ["x_btclib_node_test.py::test_x"]


def test_offenders_is_silent_once_a_terminal_fail_is_added(tmp_path: Path) -> None:
    """The same body, ended in `pytest.fail`, is a stub rather than a defect."""
    (tmp_path / "x_btclib_node_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    del node\n"
        "    require(Capability.MINE, node.capabilities, skip_counts)\n"
        "    pytest.fail('not ported for this node')\n"
    )
    assert _offenders(tmp_path) == []


def test_offenders_leaves_a_real_test_alone(tmp_path: Path) -> None:
    """A test with a real assertion is neither "bare" nor a stub."""
    (tmp_path / "x_test.py").write_text("def test_x() -> None:\n    assert 1 == 1\n")
    assert _offenders(tmp_path) == []


def test_offenders_leaves_a_docstring_only_body_alone(tmp_path: Path) -> None:
    """A function with no statement at all, docstring aside, is not flagged."""
    (tmp_path / "x_test.py").write_text(
        'def test_x() -> None:\n    """Nothing to run yet."""\n'
    )
    assert _offenders(tmp_path) == []


def test_mismatches_flags_a_capability_set_that_drifted_from_its_twin(
    tmp_path: Path,
) -> None:
    """A stub asking for less than its twin does is a reported mismatch."""
    (tmp_path / "x_btclib_node_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
        "    pytest.fail('not ported for this node')\n"
    )
    (tmp_path / "x_bitcoind_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
        "    require(Capability.B, node.capabilities, skip_counts)\n"
    )
    expected = (
        "x_btclib_node_test.py::test_x asks for ('Capability.A',), "
        "x_bitcoind_test.py::test_x asks for ('Capability.A', 'Capability.B')"
    )
    assert _mismatches(tmp_path) == [expected]


def test_mismatches_is_silent_once_the_sets_agree(tmp_path: Path) -> None:
    """An aligned pair of stub and twin is not reported."""
    (tmp_path / "x_btclib_node_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
        "    pytest.fail('not ported for this node')\n"
    )
    (tmp_path / "x_bitcoind_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
    )
    assert _mismatches(tmp_path) == []


def test_mismatches_skips_a_stub_with_no_twin_file(tmp_path: Path) -> None:
    """A stub file with no `*_bitcoind_test.py` counterpart names nothing."""
    (tmp_path / "x_btclib_node_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
        "    pytest.fail('not ported for this node')\n"
    )
    assert _mismatches(tmp_path) == []


def test_mismatches_skips_a_twin_with_no_function_of_that_name(
    tmp_path: Path,
) -> None:
    """A twin file naming no function of the stub's own name is skipped too."""
    (tmp_path / "x_btclib_node_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
        "    pytest.fail('not ported for this node')\n"
    )
    (tmp_path / "x_bitcoind_test.py").write_text(
        "def test_y(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
    )
    assert _mismatches(tmp_path) == []


def test_mismatches_ignores_a_fully_ported_test(tmp_path: Path) -> None:
    """A stub-shaped file whose test carries real logic is not compared."""
    (tmp_path / "x_btclib_node_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
        "    assert node.rpc.call('getbestblockhash')\n"
    )
    (tmp_path / "x_bitcoind_test.py").write_text(
        "def test_x(node) -> None:\n"
        "    require(Capability.A, node.capabilities, skip_counts)\n"
        "    require(Capability.B, node.capabilities, skip_counts)\n"
    )
    assert _mismatches(tmp_path) == []


def test_required_capabilities_reads_calls_in_source_order() -> None:
    """A `require` nested deeper than a later one still comes first."""
    fn = ast.parse(
        "def test_x(node) -> None:\n"
        "    if node:\n"
        "        if node:\n"
        "            require(Capability.A, node.capabilities, skip_counts)\n"
        "    require(Capability.B, node.capabilities, skip_counts)\n"
    ).body[0]
    assert isinstance(fn, ast.FunctionDef)
    assert _required_capabilities(fn) == ("Capability.A", "Capability.B")
