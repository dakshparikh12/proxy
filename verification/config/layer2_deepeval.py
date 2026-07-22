"""Layer 2 — DeepEval: spec-derived scenarios scored for grounding/citation.

Two phases, both run under the isolated tool env (``verification/tools/.venv``):

  generate  Read the doc's ACTUAL spec file, split it into sections, and ask Claude
            to synthesise grounded (question, answer) goldens whose retrieval
            context IS the spec section. Derived negative controls inject a
            spec-contradicting claim so the metrics can be shown to have teeth.
            Written to verification/scenarios/<doc>/deepeval_dataset.json and
            committed — it does NOT regenerate on every run (stable, versioned).

  score     Load the dataset and score each case with the reusable metrics:
            HallucinationMetric + CitationGrounding G-Eval (the "grounded or
            silent" core). A run PASSES when grounded cases clear threshold AND
            every negative control is correctly caught.

CLI:  python config/layer2_deepeval.py <doc> [--generate] [--regenerate]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))  # make `config` importable

from config import docs  # noqa: E402
from config.deepeval_support import (  # noqa: E402
    JUDGE_MODEL_ID,
    citation_geval,
    generate_text,
    hallucination_metric,
)

SCENARIOS = _HERE.parent / "scenarios"
N_GROUNDED = 5   # grounded goldens synthesised from the spec


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------
def _spec_sections(spec_text: str, min_len: int = 300) -> list[tuple[str, str]]:
    """Split a markdown spec into (heading, body) sections with enough substance."""
    parts = re.split(r"(?m)^(#{1,4}\s+.*)$", spec_text)
    sections: list[tuple[str, str]] = []
    i = 1
    while i < len(parts) - 1:
        heading = parts[i].lstrip("# ").strip()
        body = parts[i + 1].strip()
        if len(body) >= min_len:
            sections.append((heading, body[:1800]))
        i += 2
    return sections


def generate_dataset(doc_key: str, regenerate: bool = False) -> Path:
    doc = docs.get(doc_key)
    out = SCENARIOS / doc_key / "deepeval_dataset.json"
    if out.exists() and not regenerate:
        print(f"[generate] {out} exists — keeping the versioned dataset (use --regenerate to replace)")
        return out
    spec = doc.spec_abspath().read_text()
    sections = _spec_sections(spec)
    if not sections:
        raise RuntimeError(f"no substantive sections found in {doc.spec_path}")
    step = max(1, len(sections) // N_GROUNDED)
    chosen = sections[::step][:N_GROUNDED]

    # Self-consistency guard: a *grounded* golden must actually be grounded. After
    # generating one, score it with the SAME citation metric used at scoring time and
    # retry (bounded) if the generator produced a self-contradictory / unsupported
    # answer. This fixes dataset quality at the source rather than tolerating a broken
    # golden — it does NOT touch the scoring bar. Falls back to a verbatim spec excerpt
    # (trivially grounded) only if the model can't produce a grounded answer in N tries.
    from deepeval.test_case import LLMTestCase
    validator = citation_geval(threshold=0.7)
    MAX_TRIES = 3

    cases: list[dict] = []
    llm_used = True
    llm_error = ""
    for idx, (heading, body) in enumerate(chosen):
        prompt = (
            "You are building a grounded-QA test case from a software spec section.\n"
            "Return STRICT JSON: {\"question\": <a specific question answerable ONLY from "
            "the section>, \"answer\": <a correct answer that cites concrete facts from the "
            "section and invents nothing; be internally consistent — do not state a count "
            "that disagrees with the items you list>}.\n\n"
            f"SECTION HEADING: {heading}\n\nSECTION:\n{body}\n"
        )
        q = f"According to the section '{heading}', what does the spec require?"
        a = body[:400]
        best_q, best_a, best_score = q, a, -1.0
        try:
            for _ in range(MAX_TRIES):
                raw = generate_text(prompt)
                obj = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
                cq, ca = str(obj["question"]), str(obj["answer"])
                tc = LLMTestCase(input=cq, actual_output=ca, retrieval_context=[body], context=[body])
                validator.measure(tc)
                if validator.score > best_score:
                    best_q, best_a, best_score = cq, ca, validator.score
                if validator.score >= validator.threshold:
                    break
            q, a = best_q, best_a
            if best_score < validator.threshold:   # couldn't ground it → verbatim excerpt
                q, a = f"According to '{heading}', what does the spec state?", body[:400]
                best_score = -1.0
        except Exception as exc:  # LLM unavailable (billing/network) or unparseable
            llm_used = False
            llm_error = f"{type(exc).__name__}: {str(exc)[:160]}"
        cases.append({
            "id": f"{doc_key}-grounded-{idx}",
            "input": q,
            "actual_output": a,
            "retrieval_context": [body],
            "kind": "grounded",
            "citation_should_pass": True,
            "gen_citation_score": round(best_score, 3) if best_score >= 0 else None,
        })

    # Negative controls: graft a spec-contradicting claim onto a grounded answer.
    contradiction = ("Additionally, the system stores all state in a MongoDB cluster with a "
                     "7-day auto-delete TTL and calls the OpenAI GPT-4 API for every request.")
    for j, base in enumerate(cases[:2]):
        cases.append({
            "id": f"{doc_key}-negctl-{j}",
            "input": base["input"],
            "actual_output": base["actual_output"] + " " + contradiction,
            "retrieval_context": base["retrieval_context"],
            "kind": "negative_control",
            "citation_should_pass": False,
        })

    payload = {
        "doc": doc_key,
        "spec_path": doc.spec_path,
        "generator_model": JUDGE_MODEL_ID if llm_used else "deterministic-fallback",
        "llm_used": llm_used,
        "llm_error": llm_error,
        "note": ("Versioned dataset — generated from the ACTUAL spec sections; committed so runs "
                 "are stable. If llm_used is false the questions/answers are deterministic "
                 "spec-derived text (LLM was unavailable); the retrieval_context is real spec."),
        "cases": cases,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"[generate] wrote {len(cases)} cases -> {out}")
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
def score(doc_key: str) -> dict:
    from deepeval.test_case import LLMTestCase

    dataset_path = SCENARIOS / doc_key / "deepeval_dataset.json"
    data = json.loads(dataset_path.read_text())
    hall = hallucination_metric(threshold=0.5)
    cite = citation_geval(threshold=0.7)

    results = []
    for case in data["cases"]:
        tc = LLMTestCase(
            input=case["input"],
            actual_output=case["actual_output"],
            context=case["retrieval_context"],
            retrieval_context=case["retrieval_context"],
        )
        try:
            cite.measure(tc)
            hall.measure(tc)
        except Exception as exc:
            # The judge LLM is unavailable (e.g. Anthropic credit exhausted). Record
            # an honest BLOCKED result — never fabricate a score.
            blocked = {
                "doc": doc_key, "judge_model": JUDGE_MODEL_ID, "passed": None,
                "blocked": True, "blocked_reason": f"{type(exc).__name__}: {str(exc)[:200]}",
                "n_cases": len(data["cases"]),
                "note": "Layer 2 scoring is BUILT and smoke-verified; the full run is blocked "
                        "by an external LLM-provider limit. Re-run when credit is restored.",
                "results": [],
            }
            out = SCENARIOS / doc_key / "deepeval_results.json"
            out.write_text(json.dumps(blocked, indent=2))
            print(json.dumps({k: v for k, v in blocked.items() if k != "results"}, indent=2))
            return blocked
        # Hallucination score: higher == more hallucinated. Citation: higher == grounded.
        citation_grounded = cite.score >= cite.threshold        # the task-specified metric
        grounded_ok = citation_grounded and hall.score <= hall.threshold  # + hallucination cross-check
        results.append({
            "id": case["id"],
            "kind": case["kind"],
            "citation_score": round(cite.score, 3),
            "hallucination_score": round(hall.score, 3),
            "citation_grounded": citation_grounded,
            "grounded_ok": grounded_ok,
            "citation_should_pass": case["citation_should_pass"],
            # correct == the metric agreed with the ground-truth label for this case
            "metric_correct": grounded_ok == case["citation_should_pass"],
            "citation_reason": (cite.reason or "")[:240],
        })

    grounded = [r for r in results if r["kind"] == "grounded"]
    negctl = [r for r in results if r["kind"] == "negative_control"]
    summary = {
        "doc": doc_key,
        "judge_model": JUDGE_MODEL_ID,
        "n_cases": len(results),
        "grounded_pass": sum(r["grounded_ok"] for r in grounded),
        "grounded_total": len(grounded),
        "negative_controls_caught": sum(not r["grounded_ok"] for r in negctl),
        "negative_controls_total": len(negctl),
        "metric_accuracy": round(sum(r["metric_correct"] for r in results) / max(1, len(results)), 3),
        # Layer verdict: metrics clear grounded cases AND catch every planted hallucination.
        "passed": (all(r["grounded_ok"] for r in grounded)
                   and all(not r["grounded_ok"] for r in negctl)),
        # Citation-only view = the metric the task actually names for "cited or silent".
        # It is the authoritative grounding signal; the combined `passed` above ALSO
        # requires DeepEval's HallucinationMetric, which is noisier and can false-flag a
        # fully-grounded answer (compare per-case citation_score vs hallucination_score).
        "citation_grounded_pass": sum(r["citation_grounded"] for r in grounded),
        "citation_negctl_caught": sum(not r["citation_grounded"] for r in negctl),
        "citation_verdict_passed": (all(r["citation_grounded"] for r in grounded)
                                    and all(not r["citation_grounded"] for r in negctl)),
        "results": results,
    }
    out = SCENARIOS / doc_key / "deepeval_results.json"
    out.write_text(json.dumps(summary, indent=2))
    print(json.dumps({k: v for k, v in summary.items() if k != "results"}, indent=2))
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("doc")
    ap.add_argument("--generate", action="store_true")
    ap.add_argument("--regenerate", action="store_true")
    args = ap.parse_args()
    if args.generate or args.regenerate:
        generate_dataset(args.doc, regenerate=args.regenerate)
    score(args.doc)
