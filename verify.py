# Copyright 2026 MNC Labs, Inc.
# Author: Asaad Riaz
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Deterministic verification suite for the E2-AI boundary.

Each vector is a caller-supplied OpenAI-format payload combined with
context_evidence representing deployment facts.  No live AI call is made —
every evidence field is explicit.  The suite proves that the boundary
evaluates each governance condition correctly.

    python3 verify.py              # run all vectors
    python3 verify.py --vector 1   # run one vector
    python3 verify.py --list       # list all vectors
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("VBOS_URL", "http://localhost:8000")
API_KEY = os.environ.get("VBOS_API_KEY", "")
PROJECT_ID = os.environ.get("VBOS_PROJECT_ID", "")

RULE = "─" * 70

_connector_id_cache: str | None = None


def _resolve_ai_connector() -> str:
    global _connector_id_cache
    if _connector_id_cache:
        return _connector_id_cache

    req = urllib.request.Request(
        f"{BASE}/v1/projects/{PROJECT_ID}/connectors",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        data = json.loads(r.read())
    items = data.get("data", data.get("items", data if isinstance(data, list) else []))
    for item in items:
        if item["name"] == "Imaging AI Screening":
            _connector_id_cache = item["id"]
            return _connector_id_cache

    print("  Connector 'Imaging AI Screening' not found in project")
    sys.exit(1)


def _openai_payload(
    classification: str = "negative",
    recommended_workflow: str = "draft_for_radiologist_review",
    summary: str = "No listed abnormality identified in supplied screening observations.",
    requires_escalation: int = 0,
    model: str = "gpt-4o-2024-08-06",
    omit_classification: bool = False,
) -> dict:
    content = {
        "recommended_workflow": recommended_workflow,
        "summary": summary,
        "requires_escalation": requires_escalation,
    }
    if not omit_classification:
        content["classification"] = classification
    return {
        "model": model,
        "choices": [{"message": {"content": json.dumps(content)}}],
    }


def _context(
    study_modality: str = "CT",
    input_quality_status: str = "acceptable",
    study_instance_uid: str = "2.16.840.1.113669.632.20.1211.10000098591",
    accession_number: str = "ACC-12345",
) -> dict:
    return {
        "study_modality": study_modality,
        "input_quality_status": input_quality_status,
        "study_instance_uid": study_instance_uid,
        "accession_number": accession_number,
    }


VECTORS = {
    1: (
        "All governance conditions met",
        "ASSERT",
        "Approved model, authorized classification, governed workflow, no "
        "escalation, authorized modality, acceptable quality, traceable study.",
        {"payload": _openai_payload(), "context_evidence": _context()},
    ),
    2: (
        "Input quality unacceptable",
        "DEFER",
        "quality_is_acceptable fails. Good AI output cannot override poor "
        "input quality — degraded evidence blocks advancement.",
        {"payload": _openai_payload(), "context_evidence": _context(input_quality_status="degraded")},
    ),
    3: (
        "Unapproved model version (model drift)",
        "DEFER",
        "model_is_approved fails. The model used for inference is not in the "
        "approved_model_versions set — deployment drift detected.",
        {
            "payload": _openai_payload(model="gpt-4o-2024-11-20"),
            "context_evidence": _context(),
        },
    ),
    4: (
        "AI flags escalation with abnormal classification",
        "DEFER",
        "no_escalation_flagged and classification_is_governed both fail. The AI "
        "itself flagged escalation and classified abnormal — VB-OS honors that.",
        {
            "payload": _openai_payload(
                classification="abnormal",
                recommended_workflow="escalate_to_radiologist",
                summary="Irregular opacity identified in right upper lobe, "
                        "recommend urgent radiologist review.",
                requires_escalation=1,
            ),
            "context_evidence": _context(input_quality_status="good"),
        },
    ),
    5: (
        "Missing accession number (traceability failure)",
        "DEFER",
        "study_is_traceable fails. Every other condition passes but the study "
        "cannot be tied to an imaging order.",
        {"payload": _openai_payload(), "context_evidence": _context(accession_number="")},
    ),
    6: (
        "AI proposes out-of-contract workflow",
        "DEFER",
        "workflow_is_governed fails. Classification is valid (negative) but the "
        "AI proposes 'release_to_patient' — not in the governed vocabulary.",
        {
            "payload": _openai_payload(recommended_workflow="release_to_patient"),
            "context_evidence": _context(),
        },
    ),
    7: (
        "Missing required AI evidence (fail-closed)",
        "DEFER",
        "Admissibility failure. The AI response is missing classification "
        "entirely. Incomplete evidence cannot advance execution.",
        {
            "payload": _openai_payload(omit_classification=True),
            "context_evidence": _context(),
        },
    ),
    8: (
        "Replay verification (deterministic reproducibility)",
        "ASSERT",
        "Runs V1 evidence through gather, then replays the resulting evaluation. "
        "The replay must produce the same decision — determinism proof.",
        {"payload": _openai_payload(), "context_evidence": _context()},
    ),
    9: (
        "Unauthorized modality (mutation sensitivity)",
        "DEFER",
        "modality_is_authorized fails. Identical to V1 except study_modality "
        "is US (not in authorized_ai_modalities set). One fact changes, verdict flips.",
        {"payload": _openai_payload(), "context_evidence": _context(study_modality="US")},
    ),
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


def show_evaluation(evaluation: dict) -> None:
    print(f"    Evaluation: {evaluation.get('evaluation_id', '')}")
    print(f"      Decision: {evaluation.get('decision')}")
    print(f"      Boundary: {evaluation.get('boundary_ref')}")
    for name, result in (evaluation.get("predicate_results") or {}).items():
        passed = result.get("passed") if isinstance(result, dict) else result
        print(f"      Predicate {name}: {'PASS' if passed else 'FAIL'}")
    for reason in evaluation.get("failure_reasons") or []:
        detail = reason.get("predicate") or reason.get("field") or ""
        print(f"      Failure: {reason.get('type')}{': ' + detail if detail else ''}")


def run(number: int) -> int:
    title, expected, explanation, body = VECTORS[number]
    print(RULE)
    print(f"  V{number}: {title}")
    print(RULE)
    print(f"  {explanation}\n")

    connector_id = _resolve_ai_connector()
    response = api("POST", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}/gather", body)
    if response is None:
        return 1

    evidence = response.get("evidence") or {}
    print("  Evidence:")
    for key in sorted(evidence):
        print(f"      {key} = {evidence[key]}")

    evaluation = response.get("evaluation") or {}
    print()
    show_evaluation(evaluation)

    decision = evaluation.get("decision")
    evaluation_id = evaluation.get("evaluation_id")

    if number == 8 and evaluation_id:
        print()
        replay_result = api(
            "POST", f"/v1/projects/{PROJECT_ID}/evaluations/{evaluation_id}/replay", {}
        )
        if not replay_result:
            print("  Replay FAILED — API returned no result")
            return 1
        out = replay_result.get("replay_evaluation_output") or {}
        match = replay_result.get("result_match")
        print("  Replay verification:")
        print(f"      Original decision: {decision}")
        print(f"      Replay decision:   {out.get('decision')}")
        print(f"      Result match:      {match}")
        print(f"      Identity match:    {replay_result.get('replay_id_match')}")
        if not match:
            print("  Result: FAIL — replay did not reproduce original decision")
            print(RULE)
            return 1
        print(f"\n  Result: PASS — deterministic replay confirmed")
        print(RULE)
        return 0 if decision == expected else 1

    print(f"\n  Result: {decision} (expected {expected})")
    print(RULE)
    return 0 if decision == expected else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vector", type=int, choices=sorted(VECTORS))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()

    if args.list:
        print("E2-AI verification vectors:\n")
        for number, (title, expected, _e, _b) in VECTORS.items():
            print(f"  V{number}. {title:<50} -> {expected}")
        return 0

    if not API_KEY or not PROJECT_ID:
        print("Set VBOS_API_KEY and VBOS_PROJECT_ID (source .env first).")
        return 1

    if args.vector:
        return run(args.vector)

    failures = 0
    for number in sorted(VECTORS):
        failures += run(number)
        time.sleep(0.3)

    print(f"\n{'=' * 70}")
    passed = len(VECTORS) - failures
    print(f"  {passed}/{len(VECTORS)} vectors passed")
    print(f"{'=' * 70}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
