import sys, pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import extraction_count_gate as g  # noqa: E402


def test_agree_within_threshold(tmp_path):
    real = g.bundle_requirement_count("doc00")
    assert real > 0
    res = g.run_extraction_gate("doc00", spawn=lambda d: (real, f"EXTRACTION_COUNT: {real}"),
                                evidence_dir=tmp_path)
    assert res["halt"] is False and res["verdict"] == "AGREE"


def test_material_disagreement_halts(tmp_path):
    real = g.bundle_requirement_count("doc00")
    res = g.run_extraction_gate("doc00", spawn=lambda d: (max(1, real * 2), "EXTRACTION_COUNT: x"),
                                evidence_dir=tmp_path)
    assert res["halt"] is True and res["verdict"] == "MATERIAL_DISAGREEMENT"


def test_no_count_halts(tmp_path):
    res = g.run_extraction_gate("doc00", spawn=lambda d: (None, "agent said nothing parseable"),
                                evidence_dir=tmp_path)
    assert res["halt"] is True and res["verdict"] == "NO_COUNT"
