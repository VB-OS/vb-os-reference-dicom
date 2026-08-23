"""Run one Accessium scenario and show what the platform decided, and why.

    python3 demo.py --scenario 1
    python3 demo.py --list
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("VBOS_URL", "http://localhost:8000")
API_KEY = os.environ.get("VBOS_API_KEY", "")
PROJECT_ID = os.environ.get("VBOS_PROJECT_ID", "")

ROOT = Path(__file__).parent
STATE = json.loads((ROOT / ".bootstrap-state.json").read_text())

RULE = "─" * 70

SCENARIOS = {
    1: ("Complete accessioned CT study",
        "accessium-pacs", "E1", "ASSERT",
        "A real anonymised CT, 723 instances, carrying an accession number.",
        None),
    2: ("Study with no accession number",
        "accessium-pacs-unaccessioned", "E1", "DEFER",
        "A real MR study whose accession number is '0' — it cannot be tied to "
        "an imaging order, so there is nothing to release it against.",
        None),
    3: ("Finalised report — releases the study",
        "accessium-ris-report", "E2", "ASSERT",
        "The radiologist's report is final and linked to the order. Release is "
        "authorized, dispatched to the portal, and then verified.",
        {"context_evidence": {"authorized_instance_count": 723}}),
    4: ("Preliminary report",
        "accessium-ris-report-preliminary", "E2", "DEFER",
        "Same study, but the report has not been finalised. Nothing is released.",
        None),
    5: ("Report under an export restriction",
        "accessium-ris-report-restricted", "E2", "DEFER",
        "The report carries a confidentiality label restricting external "
        "release. This is a prohibition: evaluation stops before the rules are "
        "even considered.",
        None),
}


def api(method: str, path: str, body: dict | None = None):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.read().decode()[:200]}")
        return None


def show_evaluation(evaluation: dict, label: str) -> None:
    print(f"    {label} {evaluation.get('evaluation_id', '')}")
    print(f"      Decision: {evaluation.get('decision')}")
    print(f"      Boundary: {evaluation.get('boundary_ref')}")
    for predicate in evaluation.get("predicate_results", {}).items():
        name, result = predicate
        passed = result.get("passed") if isinstance(result, dict) else result
        print(f"      Predicate {name}: {'PASS' if passed else 'FAIL'}")
    for reason in evaluation.get("failure_reasons", []) or []:
        detail = reason.get("predicate") or reason.get("field") or ""
        print(f"      Failure: {reason.get('type')}{': ' + detail if detail else ''}")


def replay(evaluation_id: str) -> None:
    result = api("POST", f"/v1/projects/{PROJECT_ID}/evaluations/{evaluation_id}/replay", {})
    if not result:
        return
    out = result.get("replay_evaluation_output") or {}
    print(f"      Replay: {out.get('decision')} "
          f"(result match={result.get('result_match')}, "
          f"identity match={result.get('replay_id_match')})")


def run(number: int) -> int:
    title, connector, stage, expected, explanation, body = SCENARIOS[number]
    print(RULE)
    print(f"  SCENARIO {number}: {title}")
    print(RULE)
    print(f"  {explanation}\n")

    connector_id = STATE["connector_ids"][connector]
    response = api("POST", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}/gather", body or {})
    if response is None:
        return 1

    evidence = response.get("evidence") or {}
    print(f"  Evidence acquired from {connector}:")
    for key in sorted(evidence):
        print(f"      {key} = {evidence[key]}")

    evaluation = response.get("evaluation") or {}
    print()
    print(f"  Stage {stage}:")
    show_evaluation(evaluation, stage)

    evaluation_id = evaluation.get("evaluation_id")
    if evaluation_id:
        replay(evaluation_id)

    decision = evaluation.get("decision")
    print()
    if number == 3 and decision == "ASSERT":
        print("  Release authorized. Waiting for the portal to report back...")
        time.sleep(10)
        print("  Portal receipt received — see the delivery execution result above.")

    print(f"  Result: {decision} (expected {expected})")
    print(RULE)
    return 0 if decision == expected else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=int, choices=sorted(SCENARIOS))
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()

    if args.list or not args.scenario:
        print("Accessium scenarios:\n")
        for number, (title, _c, stage, expected, _e, _b) in SCENARIOS.items():
            print(f"  {number}. {title:<44} {stage} -> {expected}")
        return 0

    if not API_KEY or not PROJECT_ID:
        print("Set VBOS_API_KEY and VBOS_PROJECT_ID (source .env first).")
        return 1

    return run(args.scenario)


if __name__ == "__main__":
    sys.exit(main())
