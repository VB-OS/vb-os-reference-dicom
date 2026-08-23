"""Load real anonymized DICOM studies into the reference PACS.

Studies come from the Orthanc project's public demo archive. They are genuine
anonymized clinical studies, not synthetic fixtures.

BRAINIX is loaded partially on purpose: it carries accession number "0" in the
source data, which is what drives the E1 DEFER scenario.
"""

from __future__ import annotations

import io
import os
import sys
import urllib.request
import zipfile

DEMO_BASE = "https://orthanc.uclouvain.be/demo"
ORTHANC_URL = os.environ.get("ORTHANC_URL", "http://localhost:8042")
ORTHANC_USER = os.environ.get("ORTHANC_USER", "vbos")
ORTHANC_PASSWORD = os.environ.get("ORTHANC_PASSWORD", "accessium_dev")

STUDIES = {
    "phenix": {
        "demo_id": "49974143-ec23cb52-6b2a1c46-14d5daa0-0822ce1a",
        "description": "PHENIX — CT head/sinus, accessioned",
        "limit": None,
    },
    "brainix": {
        "demo_id": "27f7126f-4f66fb14-03f4081b-f9341db2-53925988",
        "description": "BRAINIX — MR brain, accession '0'",
        "limit": 100,
    },
}


def _auth_opener() -> urllib.request.OpenerDirector:
    mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
    mgr.add_password(None, ORTHANC_URL, ORTHANC_USER, ORTHANC_PASSWORD)
    return urllib.request.build_opener(urllib.request.HTTPBasicAuthHandler(mgr))


def upload_instance(opener: urllib.request.OpenerDirector, data: bytes) -> bool:
    req = urllib.request.Request(
        f"{ORTHANC_URL}/instances",
        data=data,
        method="POST",
        headers={"Content-Type": "application/dicom"},
    )
    try:
        with opener.open(req, timeout=60) as r:
            return r.status in (200, 201)
    except Exception:
        return False


def main() -> int:
    opener = _auth_opener()
    for name, spec in STUDIES.items():
        print(f"{name}: downloading — {spec['description']}")
        url = f"{DEMO_BASE}/studies/{spec['demo_id']}/archive"
        with urllib.request.urlopen(url, timeout=600) as r:
            archive = r.read()

        zf = zipfile.ZipFile(io.BytesIO(archive))
        members = [m for m in zf.namelist() if not m.endswith("/")]
        if spec["limit"]:
            members = members[: spec["limit"]]

        ok = 0
        for member in members:
            if upload_instance(opener, zf.read(member)):
                ok += 1
        print(f"{name}: uploaded {ok}/{len(members)} instances")

    return 0


if __name__ == "__main__":
    sys.exit(main())
