#!/usr/bin/env python3
"""Eval gate. Runs the built component on real repos and scores Tier-2 [eval] criteria vs golden keys
at thresholds. Scaffold — wire the actual build + graders (DeepEval/Braintrust) per component."""
import argparse, json, pathlib, sys
ROOT = pathlib.Path(__file__).parent

def load_scenarios(component):
    p = ROOT/"eval"/component.split("-")[0]/"scenarios.json"
    return json.loads(p.read_text()) if p.exists() else []

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--component", required=True); a = ap.parse_args()
    comp_id = a.component.split("-")[0]
    scenarios = load_scenarios(a.component)
    if not scenarios:
        print(f"NO EVAL SCENARIOS for {a.component}. Author fixtures/{comp_id}/golden/ + eval/{comp_id}/scenarios.json first (RUNBOOK STEP 5)."); sys.exit(2)
    # TODO(build): for each estate: build the component, run each scenario, score with grader, compare to threshold.
    print(f"[eval_runner] {len(scenarios)} scenarios across estates. Wire the build + grader here.")
    print("Per-criterion table (measured vs threshold) prints here; exit 0 only if ALL clear on ALL estates.")
    sys.exit(0)

if __name__ == "__main__": main()
