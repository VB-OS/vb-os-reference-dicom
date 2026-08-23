"""Referring physician portal — the system VB-OS authorizes, not part of it.

Receives a signed release authorization from a VB-OS flow, verifies it before
acting, performs the delivery, and reports back what actually happened as a FHIR
Task pushed to the platform's connector ingest.

The authorization gate is the point of the whole thing: an ASSERT arriving over
the network is not sufficient authority to act. The gateway re-fetches the
evaluation from VB-OS and checks it independently before releasing anything.

    CLAIMED -> DELIVERY_REQUESTED -> COMPLETED | TERMINAL_FAILURE
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = int(os.environ.get("GATEWAY_PORT", "9092"))
SIGNING_SECRET = os.environ.get("WEBHOOK_SIGNING_SECRET", "accessium_webhook_secret_dev")
INGEST_SECRET = os.environ.get("INGEST_SIGNING_SECRET", "accessium_ingest_secret_dev")
TIMESTAMP_TOLERANCE_SECONDS = int(os.environ.get("WEBHOOK_SKEW_SECONDS", "300"))

VBOS_URL = os.environ.get("VBOS_URL", "http://localhost:8000")
VBOS_API_KEY = os.environ.get("VBOS_API_KEY", "")
PROJECT_ID = os.environ.get("VBOS_PROJECT_ID", "")
INGEST_CONNECTOR_ID = os.environ.get("VBOS_INGEST_CONNECTOR_ID", "")

# The portal only ever acts on an authorization acquired directly from the PACS.
REQUIRED_ACQUISITION_CLASS = "active_provider"

DB_PATH = Path(__file__).parent / ".gateway.db"


def _db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS deliveries (
            delivery_id TEXT PRIMARY KEY,
            authorization_evaluation_id TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            accession TEXT,
            created_at REAL NOT NULL
        )"""
    )
    return conn


def verify_signature(body: bytes, signature: str, timestamp: str) -> tuple[bool, str]:
    if not signature or not timestamp:
        return False, "missing signature or timestamp"
    try:
        age = abs(time.time() - int(timestamp))
    except ValueError:
        return False, "malformed timestamp"
    if age > TIMESTAMP_TOLERANCE_SECONDS:
        return False, f"timestamp outside {TIMESTAMP_TOLERANCE_SECONDS}s tolerance"

    signed = f"{timestamp}.".encode() + body
    expected = "v1=" + hmac.new(
        SIGNING_SECRET.encode(), signed, hashlib.sha256
    ).hexdigest()
    # Header may carry several comma-separated signatures during rotation.
    if not any(hmac.compare_digest(expected, s.strip()) for s in signature.split(",")):
        return False, "signature mismatch"
    return True, ""


def vbos_get(path: str) -> dict | None:
    req = urllib.request.Request(
        VBOS_URL + path, headers={"Authorization": f"Bearer {VBOS_API_KEY}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError:
        return None


def verify_authorization(evaluation_id: str) -> tuple[bool, str]:
    """Re-fetch the evaluation and judge it independently of what was posted."""
    response = vbos_get(f"/v1/projects/{PROJECT_ID}/evaluations/{evaluation_id}")
    if response is None:
        return False, "authorization evaluation not found"

    # The detail endpoint nests the evaluation under "record".
    record = response.get("record", response)

    if record.get("decision") != "ASSERT":
        return False, f"authorization decision is {record.get('decision')}"

    if record.get("boundary_ref") != "B_ACCESSIUM_RELEASE_AUTHORIZATION":
        return False, f"authorization from unexpected boundary {record.get('boundary_ref')}"

    provenance = record.get("acquisition_provenance") or {}
    acquisition_class = provenance.get("acquisition_class")
    if acquisition_class != REQUIRED_ACQUISITION_CLASS:
        return False, f"acquisition class {acquisition_class!r} is not releasable"

    return True, ""


def post_receipt(task: dict) -> int:
    """Push the delivery outcome back as a signed FHIR Task."""
    body = json.dumps(task).encode()
    signature = hmac.new(INGEST_SECRET.encode(), body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(
        f"{VBOS_URL}/v1/projects/{PROJECT_ID}/connectors/{INGEST_CONNECTOR_ID}/webhook/ingest",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-VB-Signature": signature,
            "X-VB-Delivery-ID": task["id"],
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        print(f"  receipt rejected: HTTP {e.code} {e.read().decode()[:200]}")
        return e.code


def build_task(delivery_id: str, evidence: dict, delivered: int, state: str) -> dict:
    """A delivery outcome, as the portal's own FHIR Task."""
    return {
        "resourceType": "Task",
        "id": delivery_id,
        "status": state,
        "intent": "order",
        "identifier": [
            {
                "system": "http://accessium.example.org/accession",
                "value": evidence.get("report_accession", ""),
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
                "valueInteger": int(evidence.get("authorized_instance_count", delivered)),
            },
        ],
    }


def process_delivery(delivery_id: str, evaluation_id: str, evidence: dict) -> None:
    """Verify the authorization independently, deliver, and report the outcome."""
    authorized, why = verify_authorization(evaluation_id)
    if not authorized:
        print(f"  refused: {why}")
        with _db() as conn:
            conn.execute(
                "UPDATE deliveries SET state='TERMINAL_FAILURE' WHERE delivery_id=?",
                (delivery_id,),
            )
        return

    with _db() as conn:
        conn.execute(
            "UPDATE deliveries SET state='DELIVERY_REQUESTED' WHERE delivery_id=?",
            (delivery_id,),
        )

    # DELIVERY_DRIFT simulates a portal that accepts fewer instances than were
    # authorized, which is precisely what E3 exists to catch.
    authorized_count = int(evidence.get("authorized_instance_count", 0) or 0)
    drift = int(os.environ.get("DELIVERY_DRIFT", "0"))
    fail = os.environ.get("DELIVERY_FAIL", "") == "1"
    delivered = max(authorized_count - drift, 0)
    state = "failed" if fail else "completed"

    task = build_task(delivery_id, evidence, delivered, state)
    status = post_receipt(task)

    with _db() as conn:
        conn.execute(
            "UPDATE deliveries SET state=? WHERE delivery_id=?",
            ("COMPLETED" if status < 300 else "TERMINAL_FAILURE", delivery_id),
        )

    print(
        f"  delivered {delivered}/{authorized_count} as {state}"
        f" (receipt ingest HTTP {status})"
    )


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # noqa: A002 - quieten default access logging
        pass

    def _respond(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
            return
        if self.path == "/deliveries":
            with _db() as conn:
                rows = conn.execute(
                    "SELECT delivery_id, authorization_evaluation_id, state, accession"
                    " FROM deliveries ORDER BY created_at DESC"
                ).fetchall()
            self._respond(200, {"deliveries": [list(r) for r in rows]})
            return
        self._respond(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/release-study":
            self._respond(404, {"error": "not found"})
            return

        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))

        ok, why = verify_signature(
            body,
            self.headers.get("X-VBOS-Signature", ""),
            self.headers.get("X-VBOS-Timestamp", ""),
        )
        if not ok:
            print(f"  rejected: {why}")
            self._respond(401, {"error": "signature verification failed", "reason": why})
            return

        payload = json.loads(body)
        delivery_id = self.headers.get("X-VBOS-Delivery-ID", payload.get("execution_id", ""))
        evaluation_id = payload.get("evaluation_id", "")
        evidence = payload.get("evidence") or {}

        with _db() as conn:
            seen = conn.execute(
                "SELECT state FROM deliveries WHERE delivery_id = ?", (delivery_id,)
            ).fetchone()
            if seen:
                print(f"  duplicate delivery {delivery_id}")
                self._respond(200, {"status": "duplicate", "state": seen[0]})
                return

            try:
                conn.execute(
                    "INSERT INTO deliveries VALUES (?, ?, 'CLAIMED', ?, ?)",
                    (delivery_id, evaluation_id, evidence.get("report_accession"), time.time()),
                )
            except sqlite3.IntegrityError:
                # One authorization releases at most one study.
                print(f"  authorization {evaluation_id} already consumed")
                self._respond(409, {"error": "authorization already consumed"})
                return

        # Acknowledge before doing the work. The dispatching flow holds a
        # platform worker until this returns, and the authorization re-fetch
        # below calls back into that same platform — answering synchronously
        # deadlocks the two against each other.
        self._respond(202, {"status": "accepted", "delivery_id": delivery_id})
        threading.Thread(
            target=process_delivery,
            args=(delivery_id, evaluation_id, evidence),
            daemon=True,
        ).start()


def main() -> None:
    missing = [
        n
        for n, v in (
            ("VBOS_API_KEY", VBOS_API_KEY),
            ("VBOS_PROJECT_ID", PROJECT_ID),
            ("VBOS_INGEST_CONNECTOR_ID", INGEST_CONNECTOR_ID),
        )
        if not v
    ]
    if missing:
        raise SystemExit(f"missing required environment: {', '.join(missing)}")

    _db().close()
    print(f"Referring physician portal listening on :{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()  # noqa: S104


if __name__ == "__main__":
    main()
