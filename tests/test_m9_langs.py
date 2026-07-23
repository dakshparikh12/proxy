"""Multi-language graph extraction (§1 "any language", §2.2 tree-sitter tags).

Unit + property + fabricated-data coverage for the tree-sitter tag extractor:
every supported grammar yields real definition nodes, call edges (blast-radius),
and import edges — and a grammarless file is flagged honestly, never dropped.
The real-repo multi-language proof (a live non-Python clone) lives in tests/eval.
"""
from __future__ import annotations

import pathlib

import pytest

from services.code_intel import langs
from services.code_intel.graph_builder import GraphBuilder

_CASES = {
    "javascript": ("a.js", b"import {x} from './m';\nfunction charge(){}\nconst checkout = () => { charge(); };\nclass K { m(){ this.n(); } }\n",
                   {"charge", "checkout", "K", "m"}, {"charge", "n"}, {"m"}),
    "typescript": ("a.ts", b"import {x} from './m';\nexport function charge(): void {}\ninterface I {}\nfunction pay(){ charge(); }\n",
                   {"charge", "pay", "I"}, {"charge"}, {"m"}),
    "go": ("a.go", b'package p\nimport "fmt"\nfunc Charge(){}\nfunc Checkout(){ Charge(); fmt.Println() }\ntype T struct{}\n',
           {"Charge", "Checkout", "T"}, {"Charge", "Println"}, {"fmt"}),
    "java": ("A.java", b"import java.util.List;\nclass K { void charge(){} void checkout(){ charge(); } }\n",
             {"charge", "checkout", "K"}, {"charge"}, {"java.util.List"}),
    "ruby": ("a.rb", b"class K\n  def charge; end\n  def checkout; charge(); end\nend\n",
             {"charge", "checkout", "K"}, {"charge"}, set()),
    "rust": ("a.rs", b"fn charge(){}\nfn checkout(){ charge(); }\nstruct S {}\ntrait T {}\n",
             {"charge", "checkout", "S", "T"}, {"charge"}, set()),
    "c": ("a.c", b'#include "h.h"\nint charge(){ return 0; }\nvoid checkout(){ charge(); }\n',
          {"charge", "checkout"}, {"charge"}, {"h.h"}),
    "cpp": ("a.cpp", b'#include "h.h"\nclass K {};\nint charge(){ return 0; }\nvoid checkout(){ charge(); }\n',
            {"charge", "checkout", "K"}, {"charge"}, {"h.h"}),
    "csharp": ("A.cs", b"class K { void Charge(){} void Checkout(){ Charge(); } }\n",
               {"Charge", "Checkout", "K"}, {"Charge"}, set()),
    "php": ("a.php", b"<?php\nfunction charge(){}\nfunction checkout(){ charge(); }\n",
            {"charge", "checkout"}, {"charge"}, set()),
}


@pytest.mark.parametrize("lang", list(_CASES))
def test_ac_lang_001_per_language_tag_extraction(lang: str) -> None:
    """AC-LANG-001: every supported grammar extracts real definition nodes, call
    edges, and (where the grammar expresses them) import edges — no fabrication."""
    fn, src, want_defs, want_calls, want_imports = _CASES[lang]
    result = langs.extract(fn, src, lang)
    assert result is not None, f"{lang}: parse failed"
    nodes, edges = result
    defs = {n.id.split("::")[-1] for n in nodes if n.kind != "module"}
    calls = {e.target for e in edges if e.kind == "calls"}
    imports = {e.target for e in edges if e.kind == "imports"}
    assert want_defs <= defs, f"{lang}: missing defs {want_defs - defs}"
    assert want_calls <= calls, f"{lang}: missing calls {want_calls - calls}"
    assert want_imports <= imports, f"{lang}: missing imports {want_imports - imports}"
    # property: no fabricated def — every def name appears verbatim in the source.
    text = src.decode()
    assert all(name in text for name in defs), f"{lang}: fabricated def"


def test_ac_lang_008_cross_language_blast_radius(tmp_path: pathlib.Path) -> None:
    """AC-LANG-008: get_dependents (reverse-dependency blast-radius) resolves across
    languages — a symbol's callers are found in Go and in JavaScript alike."""
    (tmp_path / "util.go").write_text("package p\nfunc Helper(){}\n")
    (tmp_path / "main.go").write_text("package p\nfunc run(){ Helper() }\nfunc worker(){ Helper() }\n")
    (tmp_path / "svc.js").write_text("function charge(){}\nfunction checkout(){ charge(); }\nfunction retry(){ charge(); }\n")
    graph = GraphBuilder().build(tmp_path).graph

    def dependents(sym: str) -> set[str]:
        out: set[str] = set()
        for target in graph.resolve_symbol(sym):
            out.update(d.split("::")[-1] for d in graph.reverse_dependents(target.id))
        return out

    assert {"run", "worker"} <= dependents("Helper"), "Go blast-radius failed"
    assert {"checkout", "retry"} <= dependents("charge"), "JS blast-radius failed"


def test_ac_lang_009_grammarless_file_flagged_not_dropped(tmp_path: pathlib.Path) -> None:
    """AC-LANG-009: a grammarless file is flagged 'unsupported-language' (still
    ripgrep-searchable), never silently absent — the §3.4 coverage invariant."""
    (tmp_path / "notes.md").write_text("# hello\n")
    (tmp_path / "a.go").write_text("package p\nfunc F(){}\n")
    result = GraphBuilder().build(tmp_path)
    by_path = {r.path: r for r in result.coverage_rows}
    assert by_path["notes.md"].status == "flagged"
    assert by_path["notes.md"].flag_reason == "unsupported-language"
    assert by_path["a.go"].status == "indexed"


def test_ac_lang_010_supported_languages_span_the_major_grammars() -> None:
    """AC-LANG-010: the supported set spans the major grammars the spec's 'any repo'
    promise implies (not Python-only) — each compiles at least one working query."""
    expected = {"javascript", "typescript", "go", "java", "ruby", "rust", "c", "cpp", "csharp", "php"}
    assert expected <= set(langs._PATTERNS)
    for lang in expected:
        assert langs._compiled(lang), f"{lang}: no query compiled"
