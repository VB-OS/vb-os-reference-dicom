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

"""Idempotent VB-OS Cloud provisioning for the DICOM reference environment.

Creates and deploys the boundaries, then registers one connector per governed
subject. Safe to re-run.

Every connector is scoped to a single subject — a DICOM study by
StudyInstanceUID, a FHIR resource by direct read — so each evaluation has a
determinate subject rather than whatever the source happened to return first.

Requires VBOS_API_KEY, VBOS_PROJECT_ID and VBOS_ENVIRONMENT_ID in the
environment (see .env.example).
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = os.environ.get("VBOS_URL", "http://localhost:8000")
API_KEY = os.environ["VBOS_API_KEY"]
PROJECT_ID = os.environ["VBOS_PROJECT_ID"]
ENV_ID = os.environ["VBOS_ENVIRONMENT_ID"]


ROOT = Path(__file__).parent
STATE_FILE = ROOT / ".bootstrap-state.json"

E1 = "B_IMAGING_STUDY_ADMISSIBILITY"
E2_AI = "B_IMAGING_AI_DRAFT_AUTHORITY"
E2 = "B_IMAGING_RELEASE_AUTHORIZATION"
E3 = "B_IMAGING_DELIVERY_EXECUTION"


BOUNDARIES = [
    (
        E1,
        "Imaging Study Admissibility",
        "E1 — is this radiology study identifiable, in release scope, non-empty, "
        "and tied to an imaging order?",
    ),
    (
        E2_AI,
        "Imaging AI Draft Authority",
        "E2-AI — may this AI-produced screening result advance to the radiologist "
        "as a draft for review?",
    ),
    (
        E2,
        "Imaging Release Authorization",
        "E2 — is the report authorising this release finalised, tied to the study's "
        "accession, linked to an order, and free of an export restriction?",
    ),
    (
        E3,
        "Imaging Delivery Execution",
        "E3 — did the receiving portal complete the delivery, to the named "
        "recipient, of exactly what was authorized?",
    ),
]

PHENIX_UID = os.environ.get(
    "IMAGING_PHENIX_UID", "2.16.840.1.113669.632.20.1211.10000098591"
)
BRAINIX_UID = os.environ.get(
    "IMAGING_BRAINIX_UID", "2.16.840.1.113669.632.20.1211.10000357775"
)

DICOM_MAPPINGS = [
    {"evidence_field": "study_instance_uid", "source_path": "0020000D.Value.0"},
    {"evidence_field": "modality", "source_path": "00080061.Value.0"},
    {"evidence_field": "series_count", "source_path": "00201206.Value.0", "transform": "to_integer"},
    {"evidence_field": "instance_count", "source_path": "00201208.Value.0", "transform": "to_integer"},
    {"evidence_field": "accession_number", "source_path": "00080050.Value.0"},
]

# The portal reports its outcome as a FHIR Task pushed back to the platform.
RECEIPT_MAPPINGS = [
    {"evidence_field": "delivery_state", "source_path": "status"},
    {"evidence_field": "delivery_accession", "source_path": "identifier.0.value"},
    {"evidence_field": "recipient_reference", "source_path": "owner.reference"},
    {
        "evidence_field": "delivered_instance_count",
        "source_path": "output.0.valueInteger",
        "transform": "to_integer",
    },
    {
        "evidence_field": "authorized_instance_count",
        "source_path": "output.1.valueInteger",
        "transform": "to_integer",
    },
]

AI_SCREENING_MAPPINGS = [
    {"evidence_field": "ai_classification", "source_path": "parsed_content.classification"},
    {"evidence_field": "ai_recommended_workflow", "source_path": "parsed_content.recommended_workflow"},
    {"evidence_field": "ai_summary", "source_path": "parsed_content.summary"},
    {"evidence_field": "ai_requires_escalation", "source_path": "parsed_content.requires_escalation", "transform": "to_integer"},
    {"evidence_field": "model_used", "source_path": "model"},
]

FHIR_MAPPINGS = [
    {"evidence_field": "report_state", "source_path": "status"},
    {"evidence_field": "report_accession", "source_path": "identifier.0.value"},
    {"evidence_field": "order_reference", "source_path": "basedOn.0.reference"},
    {"evidence_field": "subject_reference", "source_path": "subject.reference"},
    {
        "evidence_field": "export_restriction",
        "source_path": "meta.security.0.code",
        "transform": "categorical_map",
        "transform_config": {"mapping": {"R": 1, "V": 1, "N": 0, "U": 0}},
    },
]


def _dicom(name: str, study_uid: str) -> dict:
    return {
        "name": name,
        "provider": "dicom",
        "boundary_ref": E1,
        "config": {"study_instance_uid": study_uid},
        "credentials": {},
        "evidence_mappings": DICOM_MAPPINGS,
        "allowed_acquisition_classes": ["caller_supplied_payload"],
    }


def _fhir(name: str, report_id: str) -> dict:
    return {
        "name": name,
        "provider": "hl7_fhir",
        "boundary_ref": E2,
        "config": {"resource_type": f"DiagnosticReport/{report_id}"},
        "credentials": {},
        "evidence_mappings": FHIR_MAPPINGS,
        "allowed_acquisition_classes": ["caller_supplied_payload"],
    }


# Ingest only. The receipt is a FHIR Task authored by the portal and pushed to
# the platform, so the acquisition is an authenticated provider push rather than
# anything the platform went and fetched.
RECEIPT_CONNECTOR = {
    "name": "imaging-portal-receipt",
    "provider": "hl7_fhir",
    "boundary_ref": E3,
    "config": {"resource_type": "Task"},
    "credentials": {},
    "evidence_mappings": RECEIPT_MAPPINGS,
    "allowed_acquisition_classes": ["caller_supplied_payload"],
}

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
INFERENCE_MODEL_ID = os.environ.get("INFERENCE_MODEL_ID", "gpt-4o-2024-08-06")

AI_SCREENING_CONNECTOR = {
    "name": "imaging-ai-screening",
    "provider": "openai",
    "boundary_ref": E2_AI,
    "config": {"model": INFERENCE_MODEL_ID},
    "credentials": {"api_key": OPENAI_API_KEY},
    "evidence_mappings": AI_SCREENING_MAPPINGS,
    "allowed_acquisition_classes": ["active_provider"],
}

CONNECTORS = [
    _dicom("imaging-pacs", PHENIX_UID),
    _dicom("imaging-pacs-unaccessioned", BRAINIX_UID),
    _fhir("imaging-ris-report", "imaging-report-phenix"),
    _fhir("imaging-ris-report-preliminary", "imaging-report-preliminary"),
    _fhir("imaging-ris-report-restricted", "imaging-report-restricted"),
    RECEIPT_CONNECTOR,
    AI_SCREENING_CONNECTOR,
]


def api(method: str, path: str, body: dict | None = None, attempts: int = 4):
    """Call the platform API, backing off when the rate limiter pushes back.

    Provisioning issues a burst of calls; the limiter allows 10 per second, so a
    clean run would otherwise fail on request eleven.
    """
    for attempt in range(attempts):
        req = urllib.request.Request(
            BASE + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < attempts - 1:
                time.sleep(1 + attempt)
                continue
            print(f"  HTTP {e.code} {method} {path}: {e.read().decode()[:300]}")
            return None
    return None


def rows(response: dict | None) -> list:
    """List endpoints return `data` or `items` depending on the resource."""
    if not response:
        return []
    return response.get("data") or response.get("items") or []


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1) + "\n")


def provision_boundary(ref: str, name: str, description: str, state: dict) -> None:
    dsl = (ROOT / "boundaries" / f"{ref}.dsl").read_text()

    listing = api("GET", f"/v1/projects/{PROJECT_ID}/boundaries") or {}
    boundary_id = next(
        (b["id"] for b in listing.get("data", []) if b["boundary_ref"] == ref), None
    )

    if not boundary_id:
        created = api("POST", f"/v1/projects/{PROJECT_ID}/boundaries", {
            "boundary_ref": ref,
            "name": name,
            "description": description,
            "dsl_source": dsl,
        })
        if created is None:
            sys.exit(f"boundary creation failed: {ref}")
        boundary_id = created["id"]
        print(f"  {ref}: created")
    else:
        print(f"  {ref}: exists")

    versions = api("GET", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions")
    latest = versions["data"][0]
    # The version list omits dsl_source; fetch the detail to compare against source.
    detail = api(
        "GET", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions/{latest['id']}"
    ) or {}

    if detail.get("dsl_source", "").strip() != dsl.strip():
        latest = api("POST", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions", {
            "dsl_source": dsl,
            "change_description": "Sync from boundaries/ source of truth",
        })
        print(f"  {ref}: new version {latest['version_number']}")

    version_id, status = latest["id"], latest["status"]

    if status == "DRAFT":
        api("POST", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions/{version_id}/submit", {})
        status = "SUBMITTED"
    if status == "SUBMITTED":
        api("POST", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions/{version_id}/approve", {})
        status = "APPROVED"

    if status in ("APPROVED", "ACTIVE"):
        deployments = api("GET", f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments") or {}
        active = any(
            d.get("boundary_version_id") == version_id and d.get("status") == "ACTIVE"
            for d in deployments.get("data", [])
        )
        if active:
            print(f"  {ref}: already deployed")
        else:
            dep = api("POST", f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments",
                      {"boundary_version_id": version_id})
            if dep and dep.get("status") == "PENDING_APPROVAL":
                api("POST",
                    f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments/{dep['id']}/approve",
                    {"comment": "Imaging reference bootstrap"})
            print(f"  {ref}: deployed")

    state.setdefault("boundaries", {})[ref] = {
        "boundary_id": boundary_id,
        "version_id": version_id,
    }


def provision_connectors(state: dict) -> None:
    listing = api("GET", f"/v1/projects/{PROJECT_ID}/connectors") or {}
    by_name = {c["name"]: c for c in listing.get("data", [])}
    connector_ids = state.get("connector_ids", {})

    for spec in CONNECTORS:
        name, config = spec["name"], spec["config"]
        existing = by_name.get(name)

        if existing:
            connector_id = existing["id"]
            patch: dict = {}
            if existing.get("config") != config:
                patch["config"] = config
            creds = spec.get("credentials", {})
            if any(v for v in creds.values()):
                patch["credentials"] = creds
            if patch:
                api("PATCH", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}", patch)
                print(f"  {name}: updated ({', '.join(patch.keys())})")
            else:
                print(f"  {name}: exists")
        else:
            body = {
                "name": name,
                "provider": spec["provider"],
                "config": config,
                "credentials": spec["credentials"],
                "allowed_acquisition_classes": spec.get(
                    "allowed_acquisition_classes", ["active_provider"]
                ),
                "evidence_mappings": spec["evidence_mappings"],
            }
            created = api("POST", f"/v1/projects/{PROJECT_ID}/connectors", body)
            if created is None:
                sys.exit(f"connector creation failed: {name}")
            connector_id = created["id"]
            print(f"  {name}: created -> {spec['boundary_ref']}")

        api("PUT", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}/schedule", {
            "auto_evaluate_enabled": True,
            "target_boundary_ref": spec["boundary_ref"],
            "target_environment_id": ENV_ID,
        })
        connector_ids[name] = connector_id

    state["connector_ids"] = connector_ids


CERTIFICATIONS = [
    (
        "Imaging Study Admissibility Certification",
        E1,
        [
            {"field": "study_instance_uid", "type": "string"},
            {"field": "modality", "type": "string"},
            {"field": "series_count", "type": "integer"},
            {"field": "instance_count", "type": "integer"},
            {"field": "accession_number", "type": "string"},
        ],
        {
            "study_instance_uid": PHENIX_UID,
            "modality": "CT",
            "series_count": 3,
            "instance_count": 723,
            "accession_number": "A10011234814",
        },
    ),
    (
        "Imaging Release Authorization Certification",
        E2,
        [
            {"field": "report_state", "type": "string"},
            {"field": "report_accession", "type": "string"},
            {"field": "order_reference", "type": "string"},
            {"field": "subject_reference", "type": "string"},
        ],
        {
            "report_state": "final",
            "report_accession": "A10011234814",
            "order_reference": "ServiceRequest/imaging-order-phenix",
            "subject_reference": "Patient/imaging-pt-phenix",
        },
    ),
    (
        "Imaging Delivery Execution Certification",
        E3,
        [
            {"field": "delivery_state", "type": "string"},
            {"field": "delivered_instance_count", "type": "integer"},
            {"field": "authorized_instance_count", "type": "integer"},
            {"field": "recipient_reference", "type": "string"},
            {"field": "delivery_accession", "type": "string"},
        ],
        {
            "delivery_state": "completed",
            "delivered_instance_count": 723,
            "authorized_instance_count": 723,
            "recipient_reference": "Practitioner/referring-physician-001",
            "delivery_accession": "A10011234814",
        },
    ),
]


def provision_certifications(state: dict) -> None:
    """A frozen conformance vector per boundary, executed on every run.

    Requires the `manage_certifications` API key scope; without it version
    creation is refused and no run is ever produced.
    """
    existing = {m["name"]: m for m in rows(
        api("GET", f"/v1/projects/{PROJECT_ID}/certification-models"))}
    model_ids = state.get("certification_model_ids", {})

    for name, boundary_ref, definitions, vector in CERTIFICATIONS:
        model = existing.get(name)
        if model:
            model_id = model["id"]
            print(f"  {name}: exists")
        else:
            created = api("POST", f"/v1/projects/{PROJECT_ID}/certification-models",
                          {"name": name, "description": f"Frozen conformance vector for {boundary_ref}"})
            if created is None:
                sys.exit(f"certification model creation failed: {name}")
            model_id = created["id"]
            print(f"  {name}: created")
        model_ids[name] = model_id

        if not rows(api("GET",
                        f"/v1/projects/{PROJECT_ID}/certification-models/{model_id}/versions")):
            version = api(
                "POST",
                f"/v1/projects/{PROJECT_ID}/certification-models/{model_id}/versions",
                {
                    "evidence_definitions": definitions,
                    "boundary_version_id": state["boundaries"][boundary_ref]["version_id"],
                    "change_description": "Initial certification model version",
                },
            )
            if version is None:
                sys.exit(f"certification version failed: {name} — check the "
                         "manage_certifications API key scope")

        run = api("POST", f"/v1/projects/{PROJECT_ID}/certify",
                  {"model": name, "environment": "Development", "evidence": vector})
        if run is None:
            sys.exit(f"certification run failed: {name}")

        status = run.get("status")
        for _ in range(15):
            if status in ("PASSED", "FAILED", "ERROR", "CANCELLED"):
                break
            time.sleep(2)
            poll = api("GET", f"/v1/projects/{PROJECT_ID}/certifications/{run['id']}")
            status = (poll or {}).get("status", status)
        print(f"    run {status}")
        if status != "PASSED":
            sys.exit(f"certification did not pass: {name} ({status})")

    state["certification_model_ids"] = model_ids


def provision_flows(state: dict) -> None:
    """Four flows: E1 intake, E2-AI draft authority, E2 release, E3 execution."""
    boundaries = state["boundaries"]
    connector_ids = state["connector_ids"]

    intake = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "config": {"connector_id": connector_ids["imaging-pacs"]},
            },
            {
                "id": "boundary-1",
                "type": "boundary",
                "config": {
                    "boundary_ref": E1,
                    "boundary_version_id": boundaries[E1]["version_id"],
                },
            },
            {
                "id": "intake-log",
                "type": "assert_action",
                "config": {
                    "action_type": "log",
                    "message": "Study admitted — ready for AI screening",
                },
            },
            {
                "id": "intake-ntf",
                "type": "defer_action",
                "config": {
                    "action_type": "notification",
                    "title": "Manual intake review required",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "boundary-1"},
            {"id": "e2", "source": "boundary-1", "target": "intake-log", "label": "assert"},
            {"id": "e3", "source": "boundary-1", "target": "intake-ntf", "label": "defer"},
        ],
    }

    ai_draft = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "config": {"connector_id": connector_ids["imaging-ai-screening"]},
            },
            {
                "id": "boundary-1",
                "type": "boundary",
                "config": {
                    "boundary_ref": E2_AI,
                    "boundary_version_id": boundaries[E2_AI]["version_id"],
                },
            },
            {
                "id": "draft-log",
                "type": "assert_action",
                "config": {
                    "action_type": "log",
                    "message": "AI draft advancement authorized for radiologist review",
                },
            },
            {
                "id": "standard-ntf",
                "type": "defer_action",
                "config": {
                    "action_type": "notification",
                    "title": "AI output not authorized — route to standard manual review",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "boundary-1"},
            {"id": "e2", "source": "boundary-1", "target": "draft-log", "label": "assert"},
            {"id": "e3", "source": "boundary-1", "target": "standard-ntf", "label": "defer"},
        ],
    }

    release = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "config": {"connector_id": connector_ids["imaging-ris-report"]},
            },
            {
                "id": "boundary-1",
                "type": "boundary",
                "config": {
                    "boundary_ref": E2,
                    "boundary_version_id": boundaries[E2]["version_id"],
                },
            },
            {
                "id": "release-log",
                "type": "assert_action",
                "config": {"action_type": "log", "message": "Study release authorized"},
            },
            {
                "id": "review-ntf",
                "type": "defer_action",
                "config": {
                    "action_type": "notification",
                    "title": "Manual release review required",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "boundary-1"},
            {"id": "e2", "source": "boundary-1", "target": "release-log", "label": "assert"},
            {"id": "e3", "source": "boundary-1", "target": "review-ntf", "label": "defer"},
        ],
    }

    execution = {
        "schema_version": 1,
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "config": {"connector_id": connector_ids["imaging-portal-receipt"]},
            },
            {
                "id": "boundary-1",
                "type": "boundary",
                "config": {
                    "boundary_ref": E3,
                    "boundary_version_id": boundaries[E3]["version_id"],
                },
            },
            {
                "id": "verified-log",
                "type": "assert_action",
                "config": {"action_type": "log", "message": "Delivery execution verified"},
            },
            {
                "id": "anomaly-ntf",
                "type": "defer_action",
                "config": {
                    "action_type": "notification",
                    "title": "Delivery execution anomaly",
                },
            },
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "boundary-1"},
            {"id": "e2", "source": "boundary-1", "target": "verified-log", "label": "assert"},
            {"id": "e3", "source": "boundary-1", "target": "anomaly-ntf", "label": "defer"},
        ],
    }

    flows = [
        ("Imaging Study Intake Governance",
         "Fires on E1. ASSERT logs study admission; application orchestrates E2-AI.",
         intake),
        ("Imaging AI Draft Authority",
         "Fires on E2-AI. ASSERT logs AI draft advancement authorization.",
         ai_draft),
        ("Imaging Release Authorization Dispatch",
         "Fires on E2. ASSERT logs release authorization for application orchestration.",
         release),
        ("Imaging Delivery Execution Result",
         "Fires on E3 from the portal receipt. ASSERT = verified, DEFER = anomaly.",
         execution),
    ]

    by_name = {f["name"]: f for f in rows(api("GET", f"/v1/projects/{PROJECT_ID}/flows"))}
    flow_ids = state.get("flow_ids", {})

    for name, description, definition in flows:
        existing = by_name.get(name)
        if existing:
            flow_id = existing["id"]
            print(f"  {name}: exists")
        else:
            created = api("POST", f"/v1/projects/{PROJECT_ID}/flows",
                          {"name": name, "description": description})
            if created is None:
                sys.exit(f"flow creation failed: {name}")
            flow_id = created["id"]
            print(f"  {name}: created")

        versions = rows(api("GET", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions"))
        if versions:
            latest = versions[0]
            detail = api("GET",
                         f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions/{latest['id']}")
            existing_def = (detail or {}).get("definition", {})
            if json.dumps(existing_def, sort_keys=True) == json.dumps(definition, sort_keys=True):
                flow_ids[name] = flow_id
                print(f"    version up to date")
                continue
            print(f"    definition changed — creating new version")

        version = api("POST", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions",
                      {"definition": definition})
        if version is None:
            sys.exit(f"flow version failed: {name}")
        vid = version["id"]
        api("POST", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions/{vid}/approve", {})
        api("PATCH", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}", {"status": "ACTIVE"})
        deployed = api("POST", f"/v1/projects/{PROJECT_ID}/flows/{flow_id}/versions/{vid}/deploy",
                       {"environment_id": ENV_ID})
        if deployed is None:
            sys.exit(f"flow deploy failed: {name}")
        print(f"  {name}: deployed")
        flow_ids[name] = flow_id

    state["flow_ids"] = flow_ids


def main() -> int:
    state = load_state()
    state["project_id"] = PROJECT_ID
    state["environment_id"] = ENV_ID

    print("Boundaries:")
    for ref, name, description in BOUNDARIES:
        provision_boundary(ref, name, description, state)

    print("Connectors:")
    provision_connectors(state)

    print("Flows:")
    provision_flows(state)

    print("Certifications:")
    provision_certifications(state)

    save_state(state)
    print(f"\nState written to {STATE_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
