"""Path-resolution helpers for the v2 build-loop decompose pipeline.

All helpers are pure functions of a doc id string (e.g. "00", "03").
"""
import pathlib

ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parent.parent


def doc_name(id: str) -> str:  # noqa: A002
    """Return the canonical doc folder name: "00" -> "doc00"."""
    return f"doc{id}"


def bundle_dir(id: str) -> pathlib.Path:  # noqa: A002
    """Return ROOT/acceptance/doc<id>."""
    return ROOT / "acceptance" / doc_name(id)


def slice_dir(id: str) -> pathlib.Path:  # noqa: A002
    """Return ROOT/slices/<id>."""
    return ROOT / "slices" / id


def spec_path(id: str) -> pathlib.Path:  # noqa: A002
    """Return the first glob match for ROOT/product/v0-spec/<id>-*.md.

    Raises FileNotFoundError if no matching file is found.
    """
    pattern = f"{id}-*.md"
    matches = sorted((ROOT / "product" / "v0-spec").glob(pattern))
    if not matches:
        raise FileNotFoundError(
            f"No spec file matching {pattern} in {ROOT / 'product' / 'v0-spec'}"
        )
    return matches[0]
