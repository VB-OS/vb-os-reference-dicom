"""Idempotent VB-OS Cloud provisioning for the Accessium DICOM reference environment.

Creates the boundary, deploys it to the target environment, and registers the
DICOM connector with its evidence mappings. Safe to re-run.

Requires VBOS_API_KEY, VBOS_PROJECT_ID and VBOS_ENVIRONMENT_ID in the
environment (see .env.example).
"""

from __future__ import annotations

import json
import os
import sys
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

ROOT = Path(__file__).parent
STATE_FILE = ROOT / ".bootstrap-state.json"

BOUNDARY_REF = "B_ACCESSIUM_STUDY_ADMISSIBILITY"
BOUNDARY_DSL = (ROOT / "boundaries" / f"{BOUNDARY_REF}.dsl").read_text()

PHENIX_UID = os.environ.get(
    "ACCESSIUM_PHENIX_UID", "2.16.840.1.113669.632.20.1211.10000098591"
)
BRAINIX_UID = os.environ.get(
    "ACCESSIUM_BRAINIX_UID", "2.16.840.1.113669.632.20.1211.10000357775"
)

# One connector per governed study. A scoped DICOM acquisition must resolve to
# exactly one study, so the evaluation subject is what was requested rather than
# whatever the archive happened to return first.
CONNECTORS = [
    ("accessium-pacs", PHENIX_UID),
    ("accessium-pacs-unaccessioned", BRAINIX_UID),
]

EVIDENCE_MAPPINGS = [
    {"evidence_field": "study_instance_uid", "source_path": "0020000D.Value.0"},
    {"evidence_field": "modality", "source_path": "00080061.Value.0"},
    {"evidence_field": "series_count", "source_path": "00201206.Value.0", "transform": "to_integer"},
    {"evidence_field": "instance_count", "source_path": "00201208.Value.0", "transform": "to_integer"},
    {"evidence_field": "accession_number", "source_path": "00080050.Value.0"},
]


def api(method: str, path: str, body: dict | None = None):
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
        print(f"  HTTP {e.code} {method} {path}: {e.read().decode()[:300]}")
        return None


def load_state() -> dict:
    return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=1) + "\n")


def provision_boundary(state: dict) -> None:
    listing = api("GET", f"/v1/projects/{PROJECT_ID}/boundaries") or {}
    boundary_id = next(
        (b["id"] for b in listing.get("data", []) if b["boundary_ref"] == BOUNDARY_REF), None
    )

    if not boundary_id:
        created = api("POST", f"/v1/projects/{PROJECT_ID}/boundaries", {
            "boundary_ref": BOUNDARY_REF,
            "name": "Accessium Study Admissibility",
            "description": (
                "E1 — is this radiology study identifiable, in release scope, "
                "non-empty, and tied to an imaging order?"
            ),
            "dsl_source": BOUNDARY_DSL,
        })
        if created is None:
            sys.exit("boundary creation failed")
        boundary_id = created["id"]
        print(f"  {BOUNDARY_REF}: created")
    else:
        print(f"  {BOUNDARY_REF}: exists")

    versions = api("GET", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions")
    latest = versions["data"][0]
    # The version list omits dsl_source; fetch the detail to compare against source.
    detail = api(
        "GET",
        f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions/{latest['id']}",
    ) or {}

    if detail.get("dsl_source", "").strip() != BOUNDARY_DSL.strip():
        latest = api("POST", f"/v1/projects/{PROJECT_ID}/boundaries/{boundary_id}/versions", {
            "dsl_source": BOUNDARY_DSL,
            "change_description": "Sync from boundaries/ source of truth",
        })
        print(f"  {BOUNDARY_REF}: new version {latest['version_number']}")

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
            print(f"  {BOUNDARY_REF}: already deployed")
        else:
            dep = api("POST", f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments",
                      {"boundary_version_id": version_id})
            if dep and dep.get("status") == "PENDING_APPROVAL":
                api("POST",
                    f"/v1/projects/{PROJECT_ID}/environments/{ENV_ID}/deployments/{dep['id']}/approve",
                    {"comment": "Accessium reference bootstrap"})
            print(f"  {BOUNDARY_REF}: deployed")

    state["boundary"] = {"boundary_id": boundary_id, "version_id": version_id}


def provision_connectors(state: dict) -> None:
    listing = api("GET", f"/v1/projects/{PROJECT_ID}/connectors") or {}
    by_name = {c["name"]: c for c in listing.get("data", [])}
    connector_ids = state.get("connector_ids", {})

    for name, study_uid in CONNECTORS:
        config = {"base_url": ORTHANC_BASE_URL, "study_instance_uid": study_uid}
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
                "provider": "dicom",
                "config": config,
                "credentials": {"username": ORTHANC_USER, "password": ORTHANC_PASSWORD},
                "allowed_acquisition_classes": ["active_provider"],
                "evidence_mappings": EVIDENCE_MAPPINGS,
            })
            if created is None:
                sys.exit(f"connector creation failed: {name}")
            connector_id = created["id"]
            print(f"  {name}: created, scoped to ...{study_uid[-6:]}")

        api("PUT", f"/v1/projects/{PROJECT_ID}/connectors/{connector_id}/schedule", {
            "auto_evaluate_enabled": True,
            "target_boundary_ref": BOUNDARY_REF,
            "target_environment_id": ENV_ID,
        })
        connector_ids[name] = connector_id

    state["connector_ids"] = connector_ids


def main() -> int:
    state = load_state()
    state["project_id"] = PROJECT_ID
    state["environment_id"] = ENV_ID

    print("Boundaries:")
    provision_boundary(state)
    print("Connectors:")
    provision_connectors(state)

    save_state(state)
    print(f"\nState written to {STATE_FILE.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
