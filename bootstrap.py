"""Idempotent VB-OS Cloud provisioning for the Accessium DICOM reference environment.

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

ORTHANC_BASE_URL = os.environ.get("ORTHANC_DICOMWEB_URL", "http://accessium-orthanc:8042/dicom-web")
ORTHANC_USER = os.environ.get("ORTHANC_USER", "vbos")
ORTHANC_PASSWORD = os.environ.get("ORTHANC_PASSWORD", "accessium_dev")
FHIR_BASE_URL = os.environ.get("FHIR_INTERNAL_URL", "http://accessium-fhir:8080/fhir")

ROOT = Path(__file__).parent
STATE_FILE = ROOT / ".bootstrap-state.json"

E1 = "B_ACCESSIUM_STUDY_ADMISSIBILITY"
E2 = "B_ACCESSIUM_RELEASE_AUTHORIZATION"

BOUNDARIES = [
    (
        E1,
        "Accessium Study Admissibility",
        "E1 — is this radiology study identifiable, in release scope, non-empty, "
        "and tied to an imaging order?",
    ),
    (
        E2,
        "Accessium Release Authorization",
        "E2 — is the report authorising this release finalised, tied to the study's "
        "accession, linked to an order, and free of an export restriction?",
    ),
]

PHENIX_UID = os.environ.get(
    "ACCESSIUM_PHENIX_UID", "2.16.840.1.113669.632.20.1211.10000098591"
)
BRAINIX_UID = os.environ.get(
    "ACCESSIUM_BRAINIX_UID", "2.16.840.1.113669.632.20.1211.10000357775"
)

DICOM_MAPPINGS = [
    {"evidence_field": "study_instance_uid", "source_path": "0020000D.Value.0"},
    {"evidence_field": "modality", "source_path": "00080061.Value.0"},
    {"evidence_field": "series_count", "source_path": "00201206.Value.0", "transform": "to_integer"},
    {"evidence_field": "instance_count", "source_path": "00201208.Value.0", "transform": "to_integer"},
    {"evidence_field": "accession_number", "source_path": "00080050.Value.0"},
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
        "config": {"base_url": ORTHANC_BASE_URL, "study_instance_uid": study_uid},
        "credentials": {"username": ORTHANC_USER, "password": ORTHANC_PASSWORD},
        "evidence_mappings": DICOM_MAPPINGS,
    }


def _fhir(name: str, report_id: str) -> dict:
    return {
        "name": name,
        "provider": "hl7_fhir",
        "boundary_ref": E2,
        # A relative resource reference performs a direct read, returning exactly
        # one resource. A bare resource type returns a Bundle whose first entry
        # is decided by server ordering.
        "config": {
            "base_url": FHIR_BASE_URL,
            "resource_type": f"DiagnosticReport/{report_id}",
        },
        # HAPI needs no auth; auth_type "bearer" with an empty token adds no
        # header. The key must still be present — an empty credential blob
        # cannot be decrypted on gather.
        "credentials": {"access_token": ""},
        "evidence_mappings": FHIR_MAPPINGS,
    }


CONNECTORS = [
    _dicom("accessium-pacs", PHENIX_UID),
    _dicom("accessium-pacs-unaccessioned", BRAINIX_UID),
    _fhir("accessium-ris-report", "accessium-report-phenix"),
    _fhir("accessium-ris-report-preliminary", "accessium-report-preliminary"),
    _fhir("accessium-ris-report-restricted", "accessium-report-restricted"),
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
                    {"comment": "Accessium reference bootstrap"})
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
            if existing.get("config") != config:
                api("PATCH", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}",
                    {"config": config})
                print(f"  {name}: scope updated")
            else:
                print(f"  {name}: exists")
        else:
            created = api("POST", f"/v1/projects/{PROJECT_ID}/connectors", {
                "name": name,
                "provider": spec["provider"],
                "config": config,
                "credentials": spec["credentials"],
                "allowed_acquisition_classes": ["active_provider"],
                "evidence_mappings": spec["evidence_mappings"],
            })
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


def main() -> int:
    state = load_state()
    state["project_id"] = PROJECT_ID
    state["environment_id"] = ENV_ID

    print("Boundaries:")
    for ref, name, description in BOUNDARIES:
        provision_boundary(ref, name, description, state)

    print("Connectors:")
    provision_connectors(state)

    save_state(state)
    print(f"\nState written to {STATE_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
