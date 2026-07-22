"""Static / introspection oracles for CROSS-SESSION-READ (no db required).

Covers the criteria whose oracle is source/route-fact inspection or token logic:
AC-CSREAD-01, -02 (structural half), -03-NEG, -04, -05 (token-gate half), -07
(structural half), -08 (structural half), -10 (determinism of the pure fold).
"""
from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path

import pytest

from scribe import notes_reader as nr
from scribe.notes_reader import (
    INTERNAL_NOTES_PATH,
    INTERNAL_RECONCILE_PATH,
    INTERNAL_ROUTE_GROUP,
    M_SURFACE_PATH,
    MOUNTS_OUTSIDE_AUTH_WALL,
    REQUIRES_USER_SESSION,
    FreshnessFlag,
    Notes,
    Response,
    read_notes,
    resolve_notes_ref,
)

_SRC = Path(nr.__file__).read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)


def _func(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in ast.walk(_TREE):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found in source")


def _calls_in(name: str) -> set[str]:
    """Dotted call targets inside a function (e.g. 'read_notes', 'Notes.fold_all')."""
    out: set[str] = set()
    for node in ast.walk(_func(name)):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                out.add(f.id)
            elif isinstance(f, ast.Attribute):
                base = f.value
                if isinstance(base, ast.Name):
                    out.add(f"{base.id}.{f.attr}")
                else:
                    out.add(f.attr)
    return out


# -- AC-CSREAD-01 -- notes_ref == meeting_id; resolver takes a ref, never an object
def test_csread_01_resolver_accepts_only_a_ref_and_a_transport() -> None:
    params = list(inspect.signature(resolve_notes_ref).parameters)
    # positional ref + keyword-only transport; NO notes-object parameter
    assert params == ["notes_ref", "http_get"]
    for p in params:
        assert "notes" not in p or p == "notes_ref"
        assert "object" not in p


def test_csread_01_resolver_targets_internal_notes_path_by_ref() -> None:
    # The resolver builds the /internal/notes/{meeting_id} path from the ref only.
    src = inspect.getsource(resolve_notes_ref)
    assert "INTERNAL_NOTES_PATH" in src
    assert "http_get" in src
    # It does not deserialise a notes object inline (no json.loads of a bundle field).
    assert "json.loads" not in src
    assert "fold_all" not in src  # never folds inline; it fetches fresh over HTTP


# -- AC-CSREAD-02 (structural) -- both handlers fold from note_deltas via ONE symbol
def test_csread_02_both_handlers_call_the_same_canonical_fold_symbol() -> None:
    internal_calls = _calls_in("internal_notes_handler")
    m_calls = _calls_in("m_handler")
    # The canonical fold-from-note_deltas symbol is read_notes (which itself calls
    # Notes.fold_all). Both handlers reference the SAME symbol.
    assert "read_notes" in internal_calls
    assert "read_notes" in m_calls
    # And read_notes is the fold entrypoint that applies Notes.fold_all.
    assert "Notes.fold_all" in _calls_in("read_notes")


def test_csread_02_notes_cache_serve_is_guarded_not_unconditional() -> None:
    # In each handler the cache serve must sit under an `if notes_cache ... in ...`
    # guard, and a read_notes (fold) path must exist as the default.
    for name in ("internal_notes_handler", "m_handler"):
        fn = _func(name)
        guarded_cache_returns = 0
        unconditional_cache_returns = 0
        for node in ast.walk(fn):
            if isinstance(node, ast.If):
                test_src = ast.dump(node.test)
                if "notes_cache" in test_src:
                    for inner in ast.walk(node):
                        if isinstance(inner, ast.Return):
                            r = ast.dump(inner)
                            if "get_bytes" in r or "notes_cache" in r:
                                guarded_cache_returns += 1
        # A get_bytes return that is NOT inside a notes_cache-guarded If would be
        # unconditional; count those.
        for node in ast.walk(fn):
            if isinstance(node, ast.Return):
                r = ast.dump(node)
                if "get_bytes" in r:
                    # find if any ancestor If tests notes_cache
                    unconditional_cache_returns += 1
        assert guarded_cache_returns >= 1, f"{name}: cache serve not guarded"
        # every get_bytes return is the guarded one (1 total, and it is guarded)
        assert unconditional_cache_returns == guarded_cache_returns


# -- AC-CSREAD-03-NEG -- missing/invalid internal token => 401, no notes body
@pytest.mark.asyncio
async def test_csread_03neg_missing_token_is_401_with_no_notes() -> None:
    for bad in (None, "", "wrong-token", 12345, object()):
        resp = await nr.internal_notes_handler(
            "any-meeting", provided_token=bad, db=_UnusedAcquirer()
        )
        assert resp.status_code == 401
        assert resp.body is None
        assert resp.is_notes_object is False


class _UnusedAcquirer:
    """An Acquirer that MUST NOT be reached (401 short-circuits before any db)."""

    def acquire(self):  # noqa: ANN201 - test double for the never-reached path
        raise AssertionError("db must not be touched when the token is rejected")


# -- AC-CSREAD-04 -- route-mount facts: /internal group, outside auth wall
def test_csread_04_route_mount_facts_outside_auth_wall() -> None:
    assert INTERNAL_NOTES_PATH == "/internal/notes/{meeting_id}"
    assert INTERNAL_NOTES_PATH.startswith(INTERNAL_ROUTE_GROUP + "/")
    assert INTERNAL_RECONCILE_PATH.startswith(INTERNAL_ROUTE_GROUP + "/")
    # Registered in the SAME route group as /internal/reconcile.
    assert INTERNAL_NOTES_PATH.split("/")[1] == INTERNAL_RECONCILE_PATH.split("/")[1] == "internal"
    assert MOUNTS_OUTSIDE_AUTH_WALL is True
    assert REQUIRES_USER_SESSION is False


# -- AC-CSREAD-05 (token-gate half) -- session cookie is never consulted here
def test_csread_05_internal_handler_signature_takes_a_token_not_a_session() -> None:
    params = list(inspect.signature(nr.internal_notes_handler).parameters)
    assert "provided_token" in params
    assert "session" not in params  # a user session is structurally never an input


@pytest.mark.asyncio
async def test_csread_05_session_cookie_alone_is_denied_on_internal_reader() -> None:
    # The internal reader accepts ONLY the internal bearer token. A "session cookie"
    # value passed as the token is not the internal token, so it is denied (401).
    resp = await nr.internal_notes_handler(
        "m", provided_token="session=valid-user-cookie", db=_UnusedAcquirer()
    )
    assert resp.status_code == 401


# -- AC-CSREAD-07 (structural) -- resolver does an HTTP GET; no inline object read
@pytest.mark.asyncio
async def test_csread_07_resolver_performs_http_get_by_ref() -> None:
    seen: dict[str, str] = {}

    async def fake_get(path: str) -> Response:
        seen["path"] = path
        return Response(status_code=200, body=json.dumps({"entries": []}))

    ref = "11111111-2222-3333-4444-555555555555"
    resp = await resolve_notes_ref(ref, http_get=fake_get)
    assert seen["path"] == f"/internal/notes/{ref}"
    assert resp.status_code == 200


def test_csread_07_resolver_has_no_notes_object_parameter() -> None:
    params = set(inspect.signature(resolve_notes_ref).parameters)
    assert params == {"notes_ref", "http_get"}
    assert "notes" not in params and "notes_object" not in params


# -- AC-CSREAD-08 (structural) -- /m/ folds via the SAME canonical symbol
def test_csread_08_m_handler_folds_via_read_notes_same_as_internal() -> None:
    assert M_SURFACE_PATH == "/m/{meeting_id}"
    assert "read_notes" in _calls_in("m_handler")
    assert "read_notes" in _calls_in("internal_notes_handler")


# -- AC-CSREAD-10 (pure-fold determinism) -- byte-identical over the same rows
def test_csread_10_fold_is_byte_stable_across_repeats_and_insertion_order() -> None:
    rows_a = [
        {"entry_id": "E1", "op": "add", "payload": json.dumps({"text": "first", "b": 2, "a": 1}), "created_at": "2026-01-01T00:00:00+00:00"},
        {"entry_id": "E2", "op": "add", "payload": json.dumps({"text": "second"}), "created_at": "2026-01-01T00:00:01+00:00"},
        {"entry_id": "E1", "op": "patch", "payload": json.dumps({"changes": {"text": "patched"}}), "created_at": "2026-01-01T00:00:02+00:00"},
    ]
    first = Notes.fold_all(rows_a).to_canonical_json()
    for _ in range(10):
        assert Notes.fold_all(rows_a).to_canonical_json() == first
    # A dict payload (already-decoded) with keys in a DIFFERENT insertion order
    # must still render identical bytes (sort_keys).
    rows_b = [
        {"entry_id": "E1", "op": "add", "payload": {"a": 1, "text": "first", "b": 2}, "created_at": "2026-01-01T00:00:00+00:00"},
        {"entry_id": "E2", "op": "add", "payload": {"text": "second"}, "created_at": "2026-01-01T00:00:01+00:00"},
        {"entry_id": "E1", "op": "patch", "payload": {"changes": {"text": "patched"}}, "created_at": "2026-01-01T00:00:02+00:00"},
    ]
    assert Notes.fold_all(rows_b).to_canonical_json() == first


def test_csread_10_unknown_op_ignored_not_fabricated() -> None:
    rows = [
        {"entry_id": "E1", "op": "add", "payload": json.dumps({"text": "x"}), "created_at": "2026-01-01T00:00:00+00:00"},
        {"entry_id": "E1", "op": "supernova", "payload": json.dumps({"text": "IGNORED"}), "created_at": "2026-01-01T00:00:01+00:00"},
    ]
    notes = Notes.fold_all(rows)
    assert notes.entries["E1"]["text"] == "x"  # unknown op did not mutate
    assert notes.freshness_flag.delta_count == 2  # but it WAS counted as a row


def test_csread_10_patch_before_add_tracks_entry_with_empty_base() -> None:
    rows = [
        {"entry_id": "E9", "op": "patch", "payload": json.dumps({"changes": {"k": "v"}}), "created_at": "2026-01-01T00:00:00+00:00"},
    ]
    notes = Notes.fold_all(rows)
    assert "E9" in notes.order
    assert notes.entries["E9"] == {"k": "v"}


def test_csread_10_close_marks_resolved_with_resolution() -> None:
    rows = [
        {"entry_id": "Q1", "op": "add", "payload": json.dumps({"text": "open?"}), "created_at": "2026-01-01T00:00:00+00:00"},
        {"entry_id": "Q1", "op": "close", "payload": json.dumps({"resolution": "answered"}), "created_at": "2026-01-01T00:00:01+00:00"},
    ]
    notes = Notes.fold_all(rows)
    assert notes.entries["Q1"]["resolved"] is True
    assert notes.entries["Q1"]["resolution"] == "answered"


def test_csread_10_freshness_flag_as_of_is_newest_created_at() -> None:
    rows = [
        {"entry_id": "E1", "op": "add", "payload": json.dumps({}), "created_at": "2026-01-01T00:00:00+00:00"},
        {"entry_id": "E2", "op": "add", "payload": json.dumps({}), "created_at": "2026-01-01T00:00:05+00:00"},
    ]
    flag = Notes.fold_all(rows).freshness_flag
    assert flag.as_of == "2026-01-01T00:00:05+00:00"
    assert flag.delta_count == 2
    assert flag.is_empty is False


def test_csread_empty_ledger_folds_to_empty_flag() -> None:
    notes = Notes.fold_all([])
    assert notes.is_empty is True
    assert notes.freshness_flag.as_of is None
    assert notes.freshness_flag.delta_count == 0
    assert notes.freshness_flag.is_empty is True


def test_response_is_notes_object_only_for_200_with_entries() -> None:
    assert Response(200, json.dumps({"entries": []})).is_notes_object is True
    assert Response(200, json.dumps({"nope": 1})).is_notes_object is False
    assert Response(404, None).is_notes_object is False
    assert Response(200, None).is_notes_object is False
    assert Response(200, "not-json").is_notes_object is False


def test_freshness_flag_to_dict_round_trips() -> None:
    f = FreshnessFlag(as_of="2026-01-01T00:00:00+00:00", delta_count=3, is_empty=False)
    assert f.to_dict() == {
        "as_of": "2026-01-01T00:00:00+00:00",
        "delta_count": 3,
        "is_empty": False,
    }
