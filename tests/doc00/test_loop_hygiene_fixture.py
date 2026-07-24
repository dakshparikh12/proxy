"""Acceptance: the autouse loop-hygiene fixture is Python-3.12-clean.

The root ``conftest._restore_current_event_loop`` fixture restores a usable
current event loop for each test after ``asyncio.run()`` nulls it. It must do so
WITHOUT ``asyncio.get_event_loop()`` in the "no current loop" path, because on
Python 3.12 that bare getter emits ``DeprecationWarning: There is no current
event loop`` and is scheduled to become a hard ``RuntimeError`` in a future
Python — a latent breakage surfaced in every async test run.

These tests drive the REAL fixture generator (imported from the repo-root
conftest) under ``warnings.simplefilter('error', DeprecationWarning)`` across
both global-loop states the fixture must survive:

  * loop explicitly nulled (``set_event_loop(None)`` — the ``asyncio.run``
    teardown case the fixture was written for), and
  * a fresh main thread that never had a loop set (the case the OLD guard let
    leak a DeprecationWarning).

The fixture must leave a usable, non-closed current loop in place and emit no
DeprecationWarning in either state.
"""
from __future__ import annotations

import asyncio
import textwrap
import warnings
from pathlib import Path

import pytest

import importlib.util

_ROOT_CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"
_spec = importlib.util.spec_from_file_location("_root_conftest_under_test", _ROOT_CONFTEST)
assert _spec is not None and _spec.loader is not None
_root_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root_conftest)

_fixture_fn = getattr(
    _root_conftest._restore_current_event_loop,
    "__wrapped__",
    _root_conftest._restore_current_event_loop,
)


def _drive_fixture_once() -> None:
    """Run the fixture generator's setup + teardown exactly once, as pytest does."""
    gen = _fixture_fn()
    next(gen)  # setup
    with pytest.raises(StopIteration):
        next(gen)  # teardown


def _save_loop_state():
    try:
        return asyncio.get_event_loop_policy().get_event_loop()
    except Exception:
        return None


def test_loop_hygiene_no_deprecation_when_loop_nulled():
    """The ``asyncio.run`` teardown case: loop explicitly nulled -> no warning."""
    saved = _save_loop_state()
    try:
        asyncio.set_event_loop(None)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            _drive_fixture_once()
        loop = asyncio.get_event_loop_policy().get_event_loop()
        assert loop is not None
        assert not loop.is_closed()
    finally:
        asyncio.set_event_loop(saved)


def test_loop_hygiene_no_deprecation_on_fresh_thread():
    """A fresh main thread with no loop ever set must not emit a warning."""
    saved = _save_loop_state()
    result: dict[str, BaseException | None] = {}

    def run_in_fresh_thread() -> None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", DeprecationWarning)
                _drive_fixture_once()
            loop = asyncio.get_event_loop_policy().get_event_loop()
            assert loop is not None
            assert not loop.is_closed()
            result["err"] = None
        except BaseException as exc:  # noqa: BLE001
            result["err"] = exc

    import threading

    t = threading.Thread(target=run_in_fresh_thread)
    t.start()
    t.join()
    asyncio.set_event_loop(saved)
    assert result["err"] is None, f"fixture leaked on fresh thread: {result['err']!r}"


def test_source_has_no_bare_get_event_loop():
    """Static guard: the fixture *code* (not its prose docstring) must not call
    the deprecated bare ``asyncio.get_event_loop()`` getter."""
    import ast
    import inspect

    src = inspect.getsource(_fixture_fn)
    tree = ast.parse(textwrap.dedent(src))
    # Drop the leading docstring expression so prose that quotes the deprecated
    # API (for explanation) does not trip the guard — we test the CODE.
    func = tree.body[0]
    assert isinstance(func, ast.FunctionDef)
    body = func.body
    if body and isinstance(body[0], ast.Expr) and isinstance(
        getattr(body[0], "value", None), ast.Constant
    ):
        body = body[1:]
    calls = [
        node
        for stmt in body
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_event_loop"
        and not node.args
    ]
    assert not calls, (
        "fixture still calls the deprecated bare asyncio.get_event_loop(); "
        "use get_running_loop()/policy._local._loop/new_event_loop() instead"
    )
