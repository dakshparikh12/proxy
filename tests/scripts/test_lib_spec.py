"""Tests for scripts/lib_spec.py — path-resolution helpers."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def test_resolves_doc03() -> None:
    import lib_spec  # noqa: PLC0415

    assert lib_spec.doc_name("03") == "doc03"
    assert lib_spec.bundle_dir("03").name == "doc03"
    assert lib_spec.spec_path("03").name.startswith("03-")
    assert lib_spec.slice_dir("03") == ROOT / "slices" / "03"
