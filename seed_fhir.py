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

"""Seed the reference RIS with the release-authorization context for each study.

Resources are written with fixed ids so the script is idempotent. Each imaging
order carries the accession number of the DICOM study it belongs to, which is
what ties E2's authorization context back to E1's study.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

FHIR_BASE = os.environ.get("FHIR_URL", "http://localhost:8090/fhir")

ACCESSION_SYSTEM = "http://imaging.example.org/accession"

PHENIX_ACCESSION = "A10011234814"

def _report(rid: str, accession: str, status: str, order: str, restricted: bool = False) -> dict:
    body = {
        "resourceType": "DiagnosticReport",
        "id": rid,
        "identifier": [{"system": ACCESSION_SYSTEM, "value": accession}],
        "status": status,
        "code": {
            "coding": [
                {"system": "http://loinc.org", "code": "24627-2", "display": "CT Head"}
            ]
        },
        "subject": {"reference": "Patient/imaging-pt-phenix"},
        "basedOn": [{"reference": f"ServiceRequest/{order}"}],
    }
    if restricted:
        # Confidentiality label restricting external release. A real
        # export hold, carried on the resource being released.
        body["meta"] = {
            "security": [
                {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-Confidentiality",
                    "code": "R",
                    "display": "restricted",
                }
            ]
        }
    return body


RESOURCES = [
    (
        "Patient/imaging-pt-phenix",
        {
            "resourceType": "Patient",
            "id": "imaging-pt-phenix",
            "identifier": [
                {"system": "http://imaging.example.org/mrn", "value": "MRN-0001"}
            ],
            "name": [{"family": "PHENIX", "given": ["Reference"]}],
        },
    ),
    (
        "ServiceRequest/imaging-order-phenix",
        {
            "resourceType": "ServiceRequest",
            "id": "imaging-order-phenix",
            "identifier": [{"system": ACCESSION_SYSTEM, "value": PHENIX_ACCESSION}],
            "status": "active",
            "intent": "order",
            "code": {
                "coding": [
                    {
                        "system": "http://loinc.org",
                        "code": "24627-2",
                        "display": "CT Head",
                    }
                ]
            },
            "subject": {"reference": "Patient/imaging-pt-phenix"},
        },
    ),
    (
        "DiagnosticReport/imaging-report-phenix",
        _report("imaging-report-phenix", PHENIX_ACCESSION, "final", "imaging-order-phenix"),
    ),
    (
        "DiagnosticReport/imaging-report-preliminary",
        _report(
            "imaging-report-preliminary",
            PHENIX_ACCESSION,
            "preliminary",
            "imaging-order-phenix",
        ),
    ),
    (
        "DiagnosticReport/imaging-report-restricted",
        _report(
            "imaging-report-restricted",
            PHENIX_ACCESSION,
            "final",
            "imaging-order-phenix",
            restricted=True,
        ),
    ),
]


def put(path: str, body: dict) -> int:
    req = urllib.request.Request(
        f"{FHIR_BASE}/{path}",
        data=json.dumps(body).encode(),
        method="PUT",
        headers={"Content-Type": "application/fhir+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        print(f"  {path}: HTTP {e.code} {e.read().decode()[:200]}")
        return e.code


def main() -> int:
    for path, body in RESOURCES:
        status = put(path, body)
        print(f"  {path}: {status}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
