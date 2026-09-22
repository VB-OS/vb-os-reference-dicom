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

"""VB-OS DICOM Live Reference Integration.

Demonstrates end-to-end governed imaging workflow against real source systems
and VB-OS Cloud. The reference integration application reads private enterprise
systems locally and submits raw source payloads to VB-OS Cloud, which performs
normalization, evidence mapping, boundary evaluation, audit, and flow execution.

    python3 demo.py --scenario 1     # run one scenario
    python3 demo.py --list           # list all scenarios
    python3 demo.py --all            # run all scenarios
    python3 demo.py --pipeline       # run the full E1 → E2-AI → E2 → E3 pipeline
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from vbos import VBOSClient

VBOS_URL = os.environ.get("VBOS_URL", "http://localhost:8000")
API_KEY = os.environ.get("VBOS_API_KEY", "")
PROJECT_ID = os.environ.get("VBOS_PROJECT_ID", "")
ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://localhost:8042")
ORTHANC_USER = os.environ.get("ORTHANC_USER", "vbos")
ORTHANC_PASSWORD = os.environ.get("ORTHANC_PASSWORD", "imaging_dev")
FHIR_URL = os.environ.get("FHIR_URL", "http://localhost:8090/fhir")
DELIVERY_DRIFT = int(os.environ.get("DELIVERY_DRIFT", "0"))

RULE = "─" * 70

_connector_cache: dict[str, str] = {}


def _resolve_connector(client: VBOSClient, name: str) -> str:
    if name in _connector_cache:
        return _connector_cache[name]

    if not _connector_cache:
        url = f"/v1/projects/{PROJECT_ID}/connectors"
        req = client._client.build_request("GET", url)
        resp = client._client.send(req)
        resp.raise_for_status()
        data = resp.json()
        for item in data.get("data", data.get("items", data if isinstance(data, list) else [])):
            _connector_cache[item["name"]] = item["id"]

    if name not in _connector_cache:
        print(f"  Connector '{name}' not found in project")
        sys.exit(1)
    return _connector_cache[name]

AI_SCREENING_PROMPT = (
    "You are a radiology screening assistant. Analyze the provided study metadata "
    "and produce a JSON object with exactly these fields:\n"
    '  "classification": one of "negative", "abnormal", or "indeterminate"\n'
    '  "recommended_workflow": one of "draft_for_radiologist_review" or '
    '"escalate_to_radiologist"\n'
    '  "summary": a one-sentence clinical summary\n'
    '  "requires_escalation": 0 if negative, 1 if abnormal or indeterminate\n'
    "Return only the JSON object, no markdown fences or commentary."
)


def _sdk_client() -> VBOSClient:
    return VBOSClient(api_key=API_KEY, base_url=VBOS_URL)


def _local_get(
    url: str,
    accept: str = "application/json",
    username: str | None = None,
    password: str | None = None,
) -> dict | list | None:
    req = urllib.request.Request(url, headers={"Accept": accept})
    if username and password:
        credentials = base64.b64encode(f"{username}:{password}".encode()).decode()
        req.add_header("Authorization", f"Basic {credentials}")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"  Local source error: HTTP {e.code} {url}")
        return None


def _read_dicomweb_study(study_uid: str) -> dict | None:
    url = f"{ORTHANC_URL}/dicom-web/studies?StudyInstanceUID={study_uid}"
    result = _local_get(
        url,
        accept="application/dicom+json",
        username=ORTHANC_USER,
        password=ORTHANC_PASSWORD,
    )
    if not result:
        return None
    if isinstance(result, list) and len(result) > 0:
        return result[0]
    return result


def _read_fhir_resource(resource_path: str) -> dict | None:
    url = f"{FHIR_URL}/{resource_path}"
    return _local_get(url)


def _build_delivery_task(
    delivery_id: str,
    accession: str,
    authorized_count: int,
    drift: int = 0,
) -> dict:
    delivered = max(authorized_count - drift, 0)
    state = "completed" if delivered == authorized_count else "completed"
    return {
        "resourceType": "Task",
        "id": delivery_id,
        "status": state,
        "intent": "order",
        "identifier": [
            {
                "system": "http://imaging.example.org/accession",
                "value": accession,
            }
        ],
        "owner": {"reference": "Practitioner/referring-physician-001"},
        "output": [
            {
                "type": {"text": "deliveredInstanceCount"},
                "valueInteger": delivered,
            },
            {
                "type": {"text": "authorizedInstanceCount"},
                "valueInteger": authorized_count,
            },
        ],
    }


def show_evaluation(evaluation: dict, label: str) -> None:
    print(f"    {label} {evaluation.get('evaluation_id', '')}")
    print(f"      Decision: {evaluation.get('decision')}")
    print(f"      Boundary: {evaluation.get('boundary_ref')}")
    for name, result in (evaluation.get("predicate_results") or {}).items():
        passed = result.get("passed") if isinstance(result, dict) else result
        print(f"      Predicate {name}: {'PASS' if passed else 'FAIL'}")
    for reason in evaluation.get("failure_reasons") or []:
        detail = reason.get("predicate") or reason.get("field") or ""
        print(f"      Failure: {reason.get('type')}{': ' + detail if detail else ''}")


def show_provenance(client: VBOSClient, evaluation_id: str) -> None:
    if not evaluation_id:
        return
    try:
        url = f"/v1/projects/{PROJECT_ID}/evaluations/{evaluation_id}"
        req = client._client.build_request("GET", url)
        resp = client._client.send(req)
        data = resp.json()
        record = data.get("record", data)
        provenance = record.get("acquisition_provenance") or {}
        acq_class = provenance.get("acquisition_class", "unknown")
        print(f"      Acquisition: {acq_class}")
    except Exception:
        pass


def verify_replay(client: VBOSClient, evaluation_id: str) -> None:
    try:
        result = client.evaluations.replay(PROJECT_ID, evaluation_id)
        raw = result.model_dump() if hasattr(result, "model_dump") else result
        replay_out = raw.get("replay_evaluation_output") or {}
        print(
            f"      Replay: {replay_out.get('decision')} "
            f"(result match={raw.get('result_match')}, "
            f"identity match={raw.get('replay_id_match')})"
        )
    except Exception as exc:
        print(f"      Replay: {exc}")


def verify_authorization(
    client: VBOSClient,
    evaluation_id: str,
    expected_boundary: str,
    expected_acquisition: str,
) -> bool:
    try:
        url = f"/v1/projects/{PROJECT_ID}/evaluations/{evaluation_id}"
        req = client._client.build_request("GET", url)
        resp = client._client.send(req)
        data = resp.json()
        record = data.get("record", data)

        if record.get("decision") != "ASSERT":
            print(f"      Authorization check: decision is {record.get('decision')}")
            return False

        if record.get("boundary_ref") != expected_boundary:
            print(f"      Authorization check: unexpected boundary {record.get('boundary_ref')}")
            return False

        provenance = record.get("acquisition_provenance") or {}
        if provenance.get("acquisition_class") != expected_acquisition:
            print(f"      Authorization check: acquisition {provenance.get('acquisition_class')}")
            return False

        print(f"      Authorization independently verified")
        return True
    except Exception as exc:
        print(f"      Authorization check failed: {exc}")
        return False


PHENIX_UID = os.environ.get(
    "IMAGING_PHENIX_UID", "2.16.840.1.113669.632.20.1211.10000098591"
)
BRAINIX_UID = os.environ.get(
    "IMAGING_BRAINIX_UID", "2.16.840.1.113669.632.20.1211.10000357775"
)


def run_e1(client: VBOSClient, study_uid: str, expected: str) -> dict | None:
    connector_id = _resolve_connector(client, "Imaging PACS")

    print("  Reading DICOMweb study from local Orthanc...")
    payload = _read_dicomweb_study(study_uid)
    if payload is None:
        print("  Failed to read study from Orthanc")
        return None

    print(f"  Submitting raw DICOMweb payload to VB-OS Cloud...")
    response = client.connectors.gather(
        project_id=PROJECT_ID,
        connector_id=connector_id,
        payload=payload,
    )
    evidence = response.get("evidence") or {}
    print(f"  Evidence extracted by VB-OS:")
    for key in sorted(evidence):
        print(f"      {key} = {evidence[key]}")

    evaluation = response.get("evaluation") or {}
    print()
    show_evaluation(evaluation, "E1")
    show_provenance(client, evaluation.get("evaluation_id"))

    decision = evaluation.get("decision")
    print(f"\n  Result: {decision} (expected {expected})")
    print(RULE)

    if decision != expected:
        return None
    return response


def run_e1_unaccessioned(client: VBOSClient) -> int:
    connector_id = _resolve_connector(client, "Imaging PACS (Unaccessioned)")

    print("  Reading DICOMweb study from local Orthanc...")
    payload = _read_dicomweb_study(BRAINIX_UID)
    if payload is None:
        print("  Failed to read study from Orthanc")
        return 1

    print(f"  Submitting raw DICOMweb payload to VB-OS Cloud...")
    response = client.connectors.gather(
        project_id=PROJECT_ID,
        connector_id=connector_id,
        payload=payload,
    )

    evidence = response.get("evidence") or {}
    print(f"  Evidence extracted by VB-OS:")
    for key in sorted(evidence):
        print(f"      {key} = {evidence[key]}")

    evaluation = response.get("evaluation") or {}
    print()
    show_evaluation(evaluation, "E1")
    show_provenance(client, evaluation.get("evaluation_id"))

    evaluation_id = evaluation.get("evaluation_id")
    if evaluation_id:
        verify_replay(client, evaluation_id)

    decision = evaluation.get("decision")
    print(f"\n  Result: {decision} (expected DEFER)")
    print(RULE)
    return 0 if decision == "DEFER" else 1


def run_e2_ai(client: VBOSClient, e1_evidence: dict) -> dict | None:
    ai_id = _resolve_connector(client, "Imaging AI Screening")

    study_context = json.dumps({
        "study_instance_uid": e1_evidence.get("study_instance_uid"),
        "modality": e1_evidence.get("modality"),
        "accession_number": e1_evidence.get("accession_number"),
        "series_count": e1_evidence.get("series_count"),
        "instance_count": e1_evidence.get("instance_count"),
    })

    print("  Invoking VB-OS OpenAI connector (active-provider)...")
    response = client.connectors.gather(
        project_id=PROJECT_ID,
        connector_id=ai_id,
        prompt=f"{AI_SCREENING_PROMPT}\n\nStudy metadata:\n{study_context}",
        response_format={"type": "json_object"},
        context_evidence={
            "study_modality": e1_evidence.get("modality", ""),
            "input_quality_status": "acceptable",
            "study_instance_uid": e1_evidence.get("study_instance_uid", ""),
            "accession_number": e1_evidence.get("accession_number", ""),
        },
    )

    evidence = response.get("evidence") or {}
    print("  AI-extracted evidence:")
    for key in sorted(evidence):
        print(f"      {key} = {evidence[key]}")

    evaluation = response.get("evaluation") or {}
    print()
    show_evaluation(evaluation, "E2-AI")
    show_provenance(client, evaluation.get("evaluation_id"))

    decision = evaluation.get("decision")
    print(f"\n  Result: {decision}")
    print(RULE)

    if decision != "ASSERT":
        return None
    return response


def run_e2_report(
    client: VBOSClient,
    connector_name: str,
    report_id: str,
    expected: str,
    context_evidence: dict | None = None,
) -> dict | None:
    connector_id = _resolve_connector(client, connector_name)

    print(f"  Reading DiagnosticReport from local HAPI FHIR...")
    payload = _read_fhir_resource(f"DiagnosticReport/{report_id}")
    if payload is None:
        print("  Failed to read report from FHIR")
        return None

    gather_kwargs = {
        "project_id": PROJECT_ID,
        "connector_id": connector_id,
        "payload": payload,
    }
    if context_evidence:
        gather_kwargs["context_evidence"] = context_evidence

    print(f"  Submitting raw FHIR payload to VB-OS Cloud...")
    response = client.connectors.gather(**gather_kwargs)

    evidence = response.get("evidence") or {}
    print(f"  Evidence extracted by VB-OS:")
    for key in sorted(evidence):
        print(f"      {key} = {evidence[key]}")

    evaluation = response.get("evaluation") or {}
    print()
    show_evaluation(evaluation, "E2")
    show_provenance(client, evaluation.get("evaluation_id"))

    evaluation_id = evaluation.get("evaluation_id")
    if evaluation_id:
        verify_replay(client, evaluation_id)

    decision = evaluation.get("decision")
    print(f"\n  Result: {decision} (expected {expected})")
    print(RULE)

    if decision != expected:
        return None
    return response


def run_e3(
    client: VBOSClient,
    e2_evaluation_id: str,
    accession: str,
    authorized_count: int,
    drift: int = 0,
) -> dict | None:
    receipt_id = _resolve_connector(client, "Imaging Portal Receipt")
    delivery_id = f"delivery-{e2_evaluation_id[:8]}"

    print("  Independent authorization verification before delivery...")
    authorized = verify_authorization(
        client, e2_evaluation_id, "B_IMAGING_RELEASE_AUTHORIZATION", "caller_supplied_payload"
    )
    if not authorized:
        print("  Delivery refused — authorization verification failed")
        return None

    print(f"  Simulating authorized delivery (drift={drift})...")
    task = _build_delivery_task(delivery_id, accession, authorized_count, drift)
    delivered = task["output"][0]["valueInteger"]
    print(f"    Delivered: {delivered}/{authorized_count} instances")

    print(f"  Submitting delivery receipt (FHIR Task) to VB-OS Cloud...")
    response = client.connectors.gather(
        project_id=PROJECT_ID,
        connector_id=receipt_id,
        payload=task,
    )

    evidence = response.get("evidence") or {}
    print(f"  Evidence extracted by VB-OS:")
    for key in sorted(evidence):
        print(f"      {key} = {evidence[key]}")

    evaluation = response.get("evaluation") or {}
    print()
    show_evaluation(evaluation, "E3")
    show_provenance(client, evaluation.get("evaluation_id"))

    evaluation_id = evaluation.get("evaluation_id")
    if evaluation_id:
        verify_replay(client, evaluation_id)

    decision = evaluation.get("decision")
    expected = "DEFER" if drift > 0 else "ASSERT"
    print(f"\n  Result: {decision} (expected {expected})")
    print(RULE)
    return response


SCENARIOS = {
    1: (
        "Complete accessioned CT study (E1 ASSERT)",
        "A real anonymised CT, 723 instances, with accession number. "
        "Raw DICOMweb payload submitted to VB-OS Cloud.",
    ),
    2: (
        "Study with no accession number (E1 DEFER)",
        "A real MR study whose accession number is '0'. Cannot be tied "
        "to an imaging order — admission is deferred.",
    ),
    3: (
        "Finalised report — release authorized (E2 ASSERT)",
        "Final DiagnosticReport linked to the order. Raw FHIR resource "
        "submitted to VB-OS Cloud. Release is authorized.",
    ),
    4: (
        "Preliminary report (E2 DEFER)",
        "Same study, but the report has not been finalised. "
        "Release is not authorized.",
    ),
    5: (
        "Report under export restriction (E2 DEFER)",
        "The report carries a confidentiality label restricting external "
        "release. Prohibition stops evaluation before rules are considered.",
    ),
    6: (
        "Live AI screening — full E1 to E2-AI pipeline",
        "Reads DICOM study from local Orthanc, submits to VB-OS E1. On ASSERT, "
        "invokes the VB-OS OpenAI connector (active-provider) for AI screening. "
        "VB-OS calls OpenAI, extracts evidence, evaluates E2-AI boundary.",
    ),
}


def run_scenario(number: int) -> int:
    title, explanation = SCENARIOS[number]
    print(RULE)
    print(f"  SCENARIO {number}: {title}")
    print(RULE)
    print(f"  {explanation}\n")

    client = _sdk_client()

    if number == 1:
        result = run_e1(client, PHENIX_UID, "ASSERT")
        if result:
            evaluation = result.get("evaluation") or {}
            eid = evaluation.get("evaluation_id")
            if eid:
                verify_replay(client, eid)
        return 0 if result else 1

    if number == 2:
        return run_e1_unaccessioned(client)

    if number == 3:
        result = run_e2_report(
            client, "Imaging RIS Report", "imaging-report-phenix", "ASSERT",
            context_evidence={"authorized_instance_count": 723},
        )
        return 0 if result else 1

    if number == 4:
        result = run_e2_report(
            client, "Imaging RIS Report (Preliminary)", "imaging-report-preliminary", "DEFER",
        )
        return 0 if result else 1

    if number == 5:
        result = run_e2_report(
            client, "Imaging RIS Report (Restricted)", "imaging-report-restricted", "DEFER",
        )
        return 0 if result else 1

    if number == 6:
        print("  Step 1: E1 Study Admissibility")
        e1 = run_e1(client, PHENIX_UID, "ASSERT")
        if e1 is None:
            print("  E1 did not ASSERT — cannot proceed to AI screening")
            return 1

        e1_evidence = e1.get("evidence") or {}
        print(f"\n  Step 2: E2-AI Draft Authority (via VB-OS OpenAI connector)")
        ai = run_e2_ai(client, e1_evidence)
        if ai is None:
            print("  E2-AI did not ASSERT")
            return 1

        ai_eval = ai.get("evaluation") or {}
        eid = ai_eval.get("evaluation_id")
        if eid:
            verify_replay(client, eid)

        print(f"\n  E2-AI decision from live AI pipeline: {ai_eval.get('decision')}")
        print(RULE)
        return 0

    return 1


def run_pipeline() -> int:
    """Full E1 → E2-AI → E2 → E3 pipeline with explicit ASSERT gates."""
    print("=" * 70)
    print("  VB-OS DICOM Live Reference Integration")
    print("  Mode: LIVE PIPELINE")
    print("=" * 70)

    client = _sdk_client()

    print(f"\n  Checking local source systems...")
    orthanc_ok = _local_get(f"{ORTHANC_URL}/system", username=ORTHANC_USER, password=ORTHANC_PASSWORD)
    fhir_ok = _local_get(f"{FHIR_URL}/metadata")
    print(f"  Orthanc ................. {'connected' if orthanc_ok else 'UNREACHABLE'}")
    print(f"  HAPI FHIR ............... {'connected' if fhir_ok else 'UNREACHABLE'}")
    if not orthanc_ok or not fhir_ok:
        print("  Cannot proceed — local source systems unavailable")
        return 1

    results = []

    # ── E1: Study Admissibility ──
    print(f"\n{'=' * 70}")
    print("  STAGE 1: E1 Study Admissibility")
    print(f"  Acquisition: caller_supplied_payload")
    print(f"{'=' * 70}\n")

    e1 = run_e1(client, PHENIX_UID, "ASSERT")
    if e1 is None:
        print("\n  E1 DEFER — pipeline halted. No downstream action permitted.")
        return 1
    e1_evidence = e1.get("evidence") or {}
    e1_eval = e1.get("evaluation") or {}
    results.append(("E1", e1_eval.get("evaluation_id"), e1_eval.get("decision")))

    # ── E2-AI: AI Draft Authority ──
    print(f"\n{'=' * 70}")
    print("  STAGE 2: E2-AI Draft Authority")
    print(f"  Acquisition: active_provider (VB-OS calls OpenAI)")
    print(f"{'=' * 70}\n")

    e2_ai = run_e2_ai(client, e1_evidence)
    if e2_ai is None:
        print("\n  E2-AI DEFER — pipeline halted. AI draft not authorized.")
        return 1
    e2_ai_eval = e2_ai.get("evaluation") or {}
    results.append(("E2-AI", e2_ai_eval.get("evaluation_id"), e2_ai_eval.get("decision")))

    # ── Simulated radiologist workflow ──
    print(f"\n{'=' * 70}")
    print("  STAGE 3: E2 Release Authorization")
    print(f"  Acquisition: caller_supplied_payload")
    print(f"  (Pre-seeded final DiagnosticReport represents completed radiologist review)")
    print(f"{'=' * 70}\n")

    e2 = run_e2_report(
        client, "Imaging RIS Report", "imaging-report-phenix", "ASSERT",
        context_evidence={"authorized_instance_count": int(e1_evidence.get("instance_count", 723))},
    )
    if e2 is None:
        print("\n  E2 DEFER — pipeline halted. Release not authorized.")
        return 1
    e2_eval = e2.get("evaluation") or {}
    results.append(("E2", e2_eval.get("evaluation_id"), e2_eval.get("decision")))

    # ── E3: Delivery Execution Verification ──
    print(f"\n{'=' * 70}")
    print("  STAGE 4: E3 Delivery Execution Verification")
    print(f"  Acquisition: caller_supplied_payload")
    print(f"  Drift: {DELIVERY_DRIFT}")
    print(f"{'=' * 70}\n")

    e2_evidence = e2.get("evidence") or {}
    e3 = run_e3(
        client,
        e2_eval.get("evaluation_id"),
        e2_evidence.get("report_accession", ""),
        int(e1_evidence.get("instance_count", 723)),
        drift=DELIVERY_DRIFT,
    )
    if e3 is None:
        print("\n  E3 — delivery receipt submission failed.")
        return 1
    e3_eval = e3.get("evaluation") or {}
    results.append(("E3", e3_eval.get("evaluation_id"), e3_eval.get("decision")))

    # ── Summary ──
    print(f"\n{'=' * 70}")
    print("  Pipeline Summary")
    print(f"{'=' * 70}")
    for stage, eid, decision in results:
        print(f"    {stage:<8} {decision:<8} {eid or ''}")
    print(f"{'=' * 70}")

    all_pass = all(
        d == ("DEFER" if DELIVERY_DRIFT > 0 and s == "E3" else "ASSERT")
        for s, _, d in results
    )
    return 0 if all_pass else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", type=int, choices=sorted(SCENARIOS))
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--pipeline", action="store_true")
    args = parser.parse_args()

    if args.list:
        print("VB-OS DICOM Live Reference Integration\n")
        print("Individual scenarios:")
        for number, (title, _) in SCENARIOS.items():
            print(f"  {number}. {title}")
        print(f"\n  --pipeline    Full E1 → E2-AI → E2 → E3 live pipeline")
        return 0

    if not API_KEY or not PROJECT_ID:
        print("Set VBOS_API_KEY and VBOS_PROJECT_ID (source .env first).")
        return 1

    if args.pipeline:
        return run_pipeline()

    if args.all:
        failures = 0
        for number in sorted(SCENARIOS):
            failures += run_scenario(number)
            time.sleep(0.3)
        print(f"\n{'=' * 70}")
        passed = len(SCENARIOS) - failures
        print(f"  {passed}/{len(SCENARIOS)} scenarios passed")
        print(f"{'=' * 70}")
        return 1 if failures else 0

    if args.scenario:
        return run_scenario(args.scenario)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
